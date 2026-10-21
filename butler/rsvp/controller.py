from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import TypeAlias

from butler.domains.result import ServiceResult, ok
from butler.domains.rsvp.domain import (
    RsvpResponse,
    add_room,
    apply_arrival_time,
    apply_botc_snapshot,
    apply_status_update,
    apply_storyteller_role,
    clear_all_rooms,
    close_room,
    prune_stale_closed_rooms,
    remove_closed_rooms,
    remove_room,
    rooms_require_rerender,
)
from butler.domains.rsvp.store import RsvpMessageStore
from butler.domains.rsvp.types import RoomEntry, RsvpStatus, ViewState, rooms_from_legacy
from butler.rooms.constants import next_room_check_iso
from butler.rooms.types import BotcJoinSnapshot
from butler.rsvp.snapshot import RsvpRenderSnapshot

logger = logging.getLogger(__name__)

SnapshotResult: TypeAlias = ServiceResult[RsvpRenderSnapshot]
ViewMutationResult: TypeAlias = ServiceResult[tuple[ViewState, RsvpRenderSnapshot]]


class RsvpController:
    def __init__(self, store: RsvpMessageStore) -> None:
        self._store = store
        self._locks: dict[int, asyncio.Lock] = {}
        self._ephemeral: dict[int, dict[int, RsvpResponse]] = {}

    def _lock_for(self, message_id: int | None) -> asyncio.Lock:
        key = message_id if message_id is not None else -1
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def bind_message(
        self,
        *,
        message_id: int,
        channel_id: int,
        guild_id: int,
        view_state: ViewState,
    ) -> None:
        self._store.upsert_message(
            message_id=message_id,
            channel_id=channel_id,
            guild_id=guild_id,
            view_state=view_state,
        )
        pending = self._ephemeral.pop(message_id, None)
        if pending:
            for user_id, response in pending.items():
                self._store.upsert_rsvp_response(
                    message_id=message_id,
                    user_id=user_id,
                    response=response,
                )

    async def get_snapshot(
        self,
        *,
        message_id: int | None,
        view_state: ViewState,
    ) -> RsvpRenderSnapshot:
        async with self._lock_for(message_id):
            return self._snapshot_unlocked(message_id=message_id, view_state=view_state)

    def _snapshot_unlocked(
        self,
        *,
        message_id: int | None,
        view_state: ViewState,
    ) -> RsvpRenderSnapshot:
        if message_id is None:
            responses = dict(self._ephemeral.get(-1, {}))
        else:
            try:
                responses = self._store.all_responses(message_id)
            except OSError:
                responses = dict(self._ephemeral.get(message_id, {}))
        rooms = view_state.rooms or rooms_from_legacy(
            room_state=view_state.room_state,
            room_url=view_state.room_url,
        )
        effective_state = view_state.with_rooms(rooms) if rooms != view_state.rooms else view_state
        return RsvpRenderSnapshot(view_state=effective_state, responses=responses)

    def _get_response(self, message_id: int | None, user_id: int) -> RsvpResponse:
        if message_id is None:
            return self._ephemeral.get(-1, {}).get(user_id) or RsvpResponse(
                status="Available",
                role="Player",
                arrival_time=None,
            )
        try:
            existing = self._store.get_rsvp_response(message_id, user_id)
        except OSError:
            existing = self._ephemeral.get(message_id, {}).get(user_id)
        return existing or RsvpResponse(status="Available", role="Player", arrival_time=None)

    def _write_response(
        self,
        *,
        message_id: int | None,
        user_id: int,
        response: RsvpResponse | None,
    ) -> None:
        if message_id is None:
            bucket = self._ephemeral.setdefault(-1, {})
            if response is None:
                bucket.pop(user_id, None)
            else:
                bucket[user_id] = response
            return
        try:
            if response is None:
                self._store.remove_rsvp_response(message_id=message_id, user_id=user_id)
            else:
                self._store.upsert_rsvp_response(
                    message_id=message_id,
                    user_id=user_id,
                    response=response,
                )
            self._ephemeral.get(message_id, {}).pop(user_id, None)
        except OSError:
            bucket = self._ephemeral.setdefault(message_id, {})
            if response is None:
                bucket.pop(user_id, None)
            else:
                bucket[user_id] = response

    def _write_view_state(self, *, message_id: int | None, view_state: ViewState) -> None:
        if message_id is None:
            return
        self._store.update_view_state(message_id=message_id, view_state=view_state)

    async def _mutate_response(
        self,
        *,
        message_id: int | None,
        view_state: ViewState,
        user_id: int,
        mutator: Callable[[RsvpResponse], RsvpResponse | None],
    ) -> SnapshotResult:
        async with self._lock_for(message_id):
            current = self._get_response(message_id, user_id)
            updated = mutator(current)
            self._write_response(message_id=message_id, user_id=user_id, response=updated)
            return ok(self._snapshot_unlocked(message_id=message_id, view_state=view_state))

    async def set_status(
        self,
        *,
        message_id: int | None,
        view_state: ViewState,
        user_id: int,
        status: RsvpStatus,
        arrival_time: str | None = None,
    ) -> SnapshotResult:
        return await self._mutate_response(
            message_id=message_id,
            view_state=view_state,
            user_id=user_id,
            mutator=lambda current: apply_status_update(
                current,
                status=status,
                arrival_time=arrival_time,
            ),
        )

    async def remove_response(
        self,
        *,
        message_id: int | None,
        view_state: ViewState,
        user_id: int,
    ) -> SnapshotResult:
        return await self._mutate_response(
            message_id=message_id,
            view_state=view_state,
            user_id=user_id,
            mutator=lambda _current: None,
        )

    async def toggle_storyteller(
        self,
        *,
        message_id: int | None,
        view_state: ViewState,
        user_id: int,
    ) -> SnapshotResult:
        async with self._lock_for(message_id):
            current = self._get_response(message_id, user_id)
            updated = apply_storyteller_role(
                current,
                is_storyteller=current.role != "Storyteller",
            )
            self._write_response(message_id=message_id, user_id=user_id, response=updated)
            return ok(self._snapshot_unlocked(message_id=message_id, view_state=view_state))

    async def set_storyteller_role(
        self,
        *,
        message_id: int | None,
        view_state: ViewState,
        user_id: int,
        is_storyteller: bool,
    ) -> SnapshotResult:
        return await self._mutate_response(
            message_id=message_id,
            view_state=view_state,
            user_id=user_id,
            mutator=lambda current: apply_storyteller_role(
                current,
                is_storyteller=is_storyteller,
            ),
        )

    async def set_arrival_time(
        self,
        *,
        message_id: int | None,
        view_state: ViewState,
        user_id: int,
        arrival_time: str,
    ) -> SnapshotResult:
        return await self._mutate_response(
            message_id=message_id,
            view_state=view_state,
            user_id=user_id,
            mutator=lambda current: apply_arrival_time(current, arrival_time=arrival_time),
        )

    def _rooms(self, view_state: ViewState) -> tuple[RoomEntry, ...]:
        if view_state.rooms:
            return view_state.rooms
        return rooms_from_legacy(
            room_state=view_state.room_state,
            room_url=view_state.room_url,
        )

    async def open_room(
        self,
        *,
        message_id: int | None,
        view_state: ViewState,
        room_url: str,
        botc: BotcJoinSnapshot | None = None,
    ) -> ViewMutationResult:
        async with self._lock_for(message_id):
            rooms = add_room(self._rooms(view_state), url=room_url, botc=botc)
            new_state = view_state.with_rooms(rooms)
            self._write_view_state(message_id=message_id, view_state=new_state)
            snapshot = self._snapshot_unlocked(message_id=message_id, view_state=new_state)
            return ok((new_state, snapshot))

    async def close_room(
        self,
        *,
        message_id: int | None,
        view_state: ViewState,
        room_id: str | None = None,
    ) -> ViewMutationResult:
        async with self._lock_for(message_id):
            current = self._rooms(view_state)
            if room_id is None:
                # Bulk ST action: drop every section and unregister from poll.
                rooms = clear_all_rooms(current)
            else:
                rooms = close_room(current, room_id=room_id)
            new_state = view_state.with_rooms(rooms)
            self._write_view_state(message_id=message_id, view_state=new_state)
            snapshot = self._snapshot_unlocked(message_id=message_id, view_state=new_state)
            return ok((new_state, snapshot))

    async def close_all_rooms(
        self,
        *,
        message_id: int | None,
        view_state: ViewState,
    ) -> ViewMutationResult:
        """Remove all room sections from the RSVP message (stops room polling)."""
        return await self.close_room(
            message_id=message_id,
            view_state=view_state,
            room_id=None,
        )

    async def remove_room(
        self,
        *,
        message_id: int | None,
        view_state: ViewState,
        room_id: str,
    ) -> ViewMutationResult:
        async with self._lock_for(message_id):
            rooms = remove_room(self._rooms(view_state), room_id=room_id)
            new_state = view_state.with_rooms(rooms)
            self._write_view_state(message_id=message_id, view_state=new_state)
            snapshot = self._snapshot_unlocked(message_id=message_id, view_state=new_state)
            return ok((new_state, snapshot))

    async def prune_closed_rooms(
        self,
        *,
        message_id: int,
        view_state: ViewState,
        now_unix: int,
        force_all_closed: bool = False,
    ) -> tuple[ViewState, RsvpRenderSnapshot, bool, int]:
        """Remove closed sections; return state, render, should_render, removed_count.

        When ``force_all_closed`` is True, every closed section is removed now
        (manual `/rooms prune`). Otherwise only stale closed rooms are dropped.
        """
        async with self._lock_for(message_id):
            current = self._rooms(view_state)
            if force_all_closed:
                pruned = remove_closed_rooms(current)
            else:
                pruned = prune_stale_closed_rooms(current, now_unix=now_unix)
            removed = len(current) - len(pruned)
            should_render = rooms_require_rerender(current, pruned)
            new_state = view_state.with_rooms(pruned)
            if pruned != current:
                self._write_view_state(message_id=message_id, view_state=new_state)
                logger.debug(
                    "room prune closed sections message_id=%s before=%s after=%s "
                    "force_all=%s",
                    message_id,
                    len(current),
                    len(pruned),
                    force_all_closed,
                )
            render = self._snapshot_unlocked(message_id=message_id, view_state=new_state)
            return new_state, render, should_render, removed

    async def apply_room_poll_results(
        self,
        *,
        message_id: int,
        view_state: ViewState,
        updates: dict[str, BotcJoinSnapshot],
        now_unix: int,
    ) -> tuple[ViewState, RsvpRenderSnapshot, tuple[RoomEntry, ...], bool]:
        """Apply botc snapshots; return state, snapshot, newly opened, should_render.

        Fetch/persist is independent of Discord rendering: ``should_render`` is True
        only when visible room section content changed (not mere last_checked stamps).
        """
        async with self._lock_for(message_id):
            current = list(self._rooms(view_state))
            before_rooms = tuple(current)
            newly_opened: list[RoomEntry] = []
            next_rooms: list[RoomEntry] = []
            checked_at = datetime.fromtimestamp(now_unix, tz=UTC)
            next_check = next_room_check_iso(now=checked_at)
            for room in current:
                snapshot = updates.get(room.room_id)
                if snapshot is None:
                    next_rooms.append(room)
                    continue
                updated, opened = apply_botc_snapshot(
                    room,
                    snapshot=snapshot,
                    now_unix=now_unix,
                )
                unchanged = updated is room
                logger.debug(
                    "room pull result message_id=%s room_id=%s "
                    "%s -> %s occupancy=%s newly_opened=%s unchanged=%s "
                    "hash_before=%s hash_after=%s edition=%r phase=%r next_check=%s",
                    message_id,
                    room.room_id,
                    room.state,
                    updated.state,
                    snapshot.occupancy,
                    opened,
                    unchanged,
                    room.content_hash,
                    updated.content_hash,
                    snapshot.edition,
                    snapshot.phase,
                    next_check,
                )
                next_rooms.append(updated)
                if opened:
                    newly_opened.append(updated)
            pruned = prune_stale_closed_rooms(next_rooms, now_unix=now_unix)
            if len(pruned) != len(next_rooms):
                logger.debug(
                    "room update pruned closed rooms message_id=%s before=%s after=%s "
                    "next_check=%s",
                    message_id,
                    len(next_rooms),
                    len(pruned),
                    next_check,
                )
            new_state = view_state.with_rooms(pruned)
            should_render = rooms_require_rerender(before_rooms, pruned)
            # Persist only when room payloads actually changed (hash/prune/backfill).
            if pruned != before_rooms:
                self._write_view_state(message_id=message_id, view_state=new_state)
            render = self._snapshot_unlocked(message_id=message_id, view_state=new_state)
            logger.debug(
                "room poll applied message_id=%s checked=%s newly_opened=%s "
                "remaining=%s should_render=%s persisted=%s checked_at=%s next_check=%s",
                message_id,
                len(updates),
                len(newly_opened),
                len(pruned),
                should_render,
                pruned != before_rooms,
                checked_at.isoformat(),
                next_check,
            )
            return new_state, render, tuple(newly_opened), should_render

    async def update_linked_event(
        self,
        *,
        message_id: int | None,
        view_state: ViewState,
        event_name: str,
        event_url: str,
        start_unix: int,
        event_card_message_id: int | None = None,
    ) -> ViewMutationResult:
        async with self._lock_for(message_id):
            new_state = replace(
                view_state,
                event_name=event_name,
                event_url=event_url,
                start_unix=start_unix,
                event_card_message_id=(
                    event_card_message_id
                    if event_card_message_id is not None
                    else view_state.event_card_message_id
                ),
            )
            self._write_view_state(message_id=message_id, view_state=new_state)
            snapshot = self._snapshot_unlocked(message_id=message_id, view_state=new_state)
            return ok((new_state, snapshot))

    async def user_ids_for_status(
        self,
        *,
        message_id: int | None,
        view_state: ViewState,
        status: RsvpStatus,
    ) -> list[int]:
        snapshot = await self.get_snapshot(message_id=message_id, view_state=view_state)
        return [
            user_id
            for user_id, response in snapshot.responses.items()
            if response.status == status
        ]
