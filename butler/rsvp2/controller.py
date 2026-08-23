from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from typing import TypeAlias

from butler.domains.result import ServiceResult, ok
from butler.domains.rsvp.domain import (
    RoomSnapshot,
    RsvpResponse,
    apply_arrival_time,
    apply_status_update,
    apply_storyteller_role,
)
from butler.domains.rsvp.store import RsvpMessageStore
from butler.domains.rsvp.types import RsvpStatus, ViewState
from butler.rsvp2.snapshot import RsvpRenderSnapshot

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
        room_url = view_state.room_url if view_state.room_state == "open" else None
        effective_state = replace(view_state, room_url=room_url)
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

    async def open_room(
        self,
        *,
        message_id: int | None,
        view_state: ViewState,
        room_url: str,
    ) -> ViewMutationResult:
        async with self._lock_for(message_id):
            room = RoomSnapshot.from_url(room_url)
            new_state = replace(view_state, room_state=room.state, room_url=room.url)
            self._write_view_state(message_id=message_id, view_state=new_state)
            snapshot = self._snapshot_unlocked(message_id=message_id, view_state=new_state)
            return ok((new_state, snapshot))

    async def close_room(
        self,
        *,
        message_id: int | None,
        view_state: ViewState,
    ) -> ViewMutationResult:
        async with self._lock_for(message_id):
            room = RoomSnapshot.closed()
            new_state = replace(view_state, room_state=room.state, room_url=room.url)
            self._write_view_state(message_id=message_id, view_state=new_state)
            snapshot = self._snapshot_unlocked(message_id=message_id, view_state=new_state)
            return ok((new_state, snapshot))

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
