from __future__ import annotations

import hashlib
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Final
from uuid import uuid4

from butler.design import STORYTELLER_EMOJI
from butler.domains.rsvp.types import (
    ROOM_CLOSED_PRUNE_SECONDS,
    RoomButton,
    RoomEntry,
    RoomState,
    RsvpRole,
    RsvpStatus,
)
from butler.rooms.constants import ROOM_EMPTY_ABANDON_SECONDS
from butler.rooms.types import BotcJoinSnapshot
from butler.rooms.urls import botc_join_code, is_botc_join_url

REACTION_STATUS_PRECEDENCE: Final[tuple[RsvpStatus, ...]] = (
    "Maybe",
    "Cant",
    "Available",
)


@dataclass(frozen=True)
class RoomSnapshot:
    state: RoomState
    url: str | None

    @classmethod
    def from_url(cls, room_url: str | None) -> RoomSnapshot:
        if room_url is None:
            return cls(state="pending", url=None)
        return cls(state="open", url=room_url)

    @classmethod
    def closed(cls) -> RoomSnapshot:
        return cls(state="closed", url=None)


def new_room_id(url: str) -> str:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    return f"r_{digest}_{uuid4().hex[:6]}"


def visible_room_buttons(rooms: Sequence[RoomEntry]) -> frozenset[RoomButton]:
    buttons: set[RoomButton] = {"open_or_prompt"}
    if rooms:
        buttons.add("remove")
        # Close-all lives on the main ST row whenever any room is attached,
        # so pending/closed rooms are not stuck without a bulk action.
        buttons.add("close_all")
    if any(room.state == "open" for room in rooms):
        buttons.add("close")
    return frozenset(buttons)


def open_room_ids(rooms: Sequence[RoomEntry]) -> tuple[str, ...]:
    return tuple(room.room_id for room in rooms if room.state == "open")


def add_room(
    rooms: Sequence[RoomEntry],
    *,
    url: str,
    now_unix: int | None = None,
    botc: BotcJoinSnapshot | None = None,
) -> tuple[RoomEntry, ...]:
    normalized = url.strip()
    for existing in rooms:
        if existing.url.casefold() == normalized.casefold():
            return tuple(rooms)

    label = botc_join_code(normalized) if is_botc_join_url(normalized) else None
    stamp = int(time.time() if now_unix is None else now_unix)
    if botc is not None and botc.occupancy == "closed":
        state: RoomState = "closed"
    elif botc is not None and botc.occupancy == "open":
        state = "open"
    elif is_botc_join_url(normalized) and botc is None:
        # Prefer usable open link over endless "waiting" when fetch failed.
        state = "open"
    else:
        state = "open"

    open_botc = botc if state == "open" and botc is not None else None
    empty_since = (
        stamp if open_botc is not None and _snapshot_is_empty(open_botc) else None
    )
    entry = with_room_content_hash(
        RoomEntry(
            room_id=new_room_id(normalized),
            url=normalized,
            label=label,
            state=state,
            source="polled" if is_botc_join_url(normalized) else "manual",
            last_checked_unix=stamp if botc is not None else None,
            closed_at_unix=stamp if state == "closed" else None,
            empty_since_unix=empty_since,
            botc=open_botc,
        )
    )
    return (*rooms, entry)


def remove_room(
    rooms: Sequence[RoomEntry],
    *,
    room_id: str,
) -> tuple[RoomEntry, ...]:
    """Detach a room from the RSVP message entirely (decouple)."""
    return tuple(room for room in rooms if room.room_id != room_id)


def close_room(
    rooms: Sequence[RoomEntry],
    *,
    room_id: str,
    now_unix: int | None = None,
) -> tuple[RoomEntry, ...]:
    stamp = int(time.time() if now_unix is None else now_unix)
    updated: list[RoomEntry] = []
    for room in rooms:
        if room.room_id != room_id:
            updated.append(room)
            continue
        updated.append(
            with_room_content_hash(
                replace(
                    room,
                    state="closed",
                    botc=None,
                    closed_at_unix=room.closed_at_unix or stamp,
                    empty_since_unix=None,
                )
            )
        )
    return tuple(updated)


def close_all_open_rooms(
    rooms: Sequence[RoomEntry],
    *,
    now_unix: int | None = None,
) -> tuple[RoomEntry, ...]:
    """Mark every open room closed (keep sections); drop pending rooms."""
    stamp = int(time.time() if now_unix is None else now_unix)
    updated: list[RoomEntry] = []
    for room in rooms:
        if room.state == "pending":
            continue
        if room.state != "open":
            updated.append(room)
            continue
        updated.append(
            with_room_content_hash(
                replace(
                    room,
                    state="closed",
                    botc=None,
                    closed_at_unix=room.closed_at_unix or stamp,
                    empty_since_unix=None,
                )
            )
        )
    return tuple(updated)


def clear_all_rooms(rooms: Sequence[RoomEntry]) -> tuple[RoomEntry, ...]:
    """Remove every room section and stop tracking/polling them."""
    _ = rooms
    return ()


def format_room_section(rooms: Sequence[RoomEntry]) -> str | None:
    if not rooms:
        return None
    return "\n\n".join(format_room_block(room) for room in rooms)


def format_room_block(room: RoomEntry) -> str:
    """Markdown block for one room (used inside a LayoutView Section/Container)."""
    # Heading-sized label; bare URL stays plain so Discord keeps it copyable.
    lines = [f"### Rumslänk: {room.url}"]
    if room.state == "pending":
        lines.append("Väntar på uppdatering…")
        return "\n".join(lines)
    if room.state == "closed":
        lines.append("Stängt")
        return "\n".join(lines)

    botc = room.botc
    if botc is not None:
        lines.extend(_format_open_botc_lines(botc))
    return "\n".join(lines)


def _snapshot_is_empty(snapshot: BotcJoinSnapshot) -> bool:
    return len(snapshot.filled_players) == 0 and len(snapshot.storytellers) == 0


def _filled_player_count(snapshot: BotcJoinSnapshot | None) -> int:
    if snapshot is None:
        return 0
    return len(snapshot.filled_players)


def room_track_signature(room: RoomEntry) -> tuple[object, ...]:
    """Fields that trigger RSVP Discord updates while tracking an open room."""
    if room.state != "open":
        return (room.state, room.url)
    botc = room.botc
    return (
        room.state,
        room.url,
        None if botc is None else botc.phase,
        None if botc is None else botc.edition,
        _filled_player_count(botc),
    )


def room_content_hash(room: RoomEntry) -> str:
    """Hash of tracked room fields: state, phase, player count, edition."""
    payload = repr(room_track_signature(room)).encode()
    return hashlib.sha256(payload).hexdigest()[:32]


def with_room_content_hash(room: RoomEntry) -> RoomEntry:
    digest = room_content_hash(room)
    if room.content_hash == digest:
        return room
    return replace(room, content_hash=digest)


def _close_tracked_room(
    room: RoomEntry,
    *,
    now_unix: int,
    label: str | None,
) -> RoomEntry:
    return with_room_content_hash(
        replace(
            room,
            state="closed",
            label=label if label is not None else room.label,
            last_checked_unix=now_unix,
            closed_at_unix=room.closed_at_unix or now_unix,
            empty_since_unix=None,
            botc=None,
            source="polled",
        )
    )


def apply_botc_snapshot(
    room: RoomEntry,
    *,
    snapshot: BotcJoinSnapshot,
    now_unix: int,
    empty_abandon_seconds: int = ROOM_EMPTY_ABANDON_SECONDS,
) -> tuple[RoomEntry, bool]:
    """Return (updated_room, newly_opened).

    Tracks open rooms until abandoned:
    - botc occupancy closed, or
    - 0 filled players and 0 storytellers for ``empty_abandon_seconds``.

    Discord re-render is driven by content_hash (state/phase/player count/edition).
    """
    previous = room.state
    label = snapshot.code or room.label

    if snapshot.occupancy == "closed":
        candidate = _close_tracked_room(room, now_unix=now_unix, label=label)
    elif snapshot.occupancy != "open":
        return replace(room, last_checked_unix=now_unix), False
    else:
        empty = _snapshot_is_empty(snapshot)
        if empty:
            empty_since = room.empty_since_unix or now_unix
            if now_unix - empty_since >= empty_abandon_seconds:
                candidate = _close_tracked_room(room, now_unix=now_unix, label=label)
            else:
                candidate = with_room_content_hash(
                    replace(
                        room,
                        state="open",
                        label=label,
                        last_checked_unix=now_unix,
                        closed_at_unix=None,
                        empty_since_unix=empty_since,
                        botc=snapshot,
                        source="polled",
                    )
                )
        else:
            candidate = with_room_content_hash(
                replace(
                    room,
                    state="open",
                    label=label,
                    last_checked_unix=now_unix,
                    closed_at_unix=None,
                    empty_since_unix=None,
                    botc=snapshot,
                    source="polled",
                )
            )

    newly_opened = previous != "open" and candidate.state == "open"
    if room_track_signature(room) == room_track_signature(candidate):
        # No Discord-tracked field change; still refresh timers/roster payload.
        if (
            room.last_checked_unix == candidate.last_checked_unix
            and room.empty_since_unix == candidate.empty_since_unix
            and room.botc == candidate.botc
            and room.label == candidate.label
            and room.content_hash == candidate.content_hash
            and room.state == candidate.state
        ):
            return room, False
        return candidate, False
    return candidate, newly_opened



def rooms_content_hash(rooms: Sequence[RoomEntry]) -> str:
    """Hash of all room visible sections (order-sensitive)."""
    payload = "|".join(
        room.content_hash or room_content_hash(room) for room in rooms
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:32]


def rooms_require_rerender(
    before: Sequence[RoomEntry],
    after: Sequence[RoomEntry],
) -> bool:
    """True when Discord room sections would look/behave differently."""
    return rooms_content_hash(before) != rooms_content_hash(after)


def prune_stale_closed_rooms(
    rooms: Sequence[RoomEntry],
    *,
    now_unix: int,
    max_closed_age: int = ROOM_CLOSED_PRUNE_SECONDS,
) -> tuple[RoomEntry, ...]:
    """Drop closed room sections past max age (untracks them from poll)."""
    kept: list[RoomEntry] = []
    for room in rooms:
        if room.state != "closed":
            kept.append(room)
            continue
        closed_at = room.closed_at_unix
        if closed_at is None:
            closed_at = room.last_checked_unix
        if closed_at is None:
            # Unknown close time: treat as already stale so the section goes away.
            continue
        if now_unix - closed_at >= max_closed_age:
            continue
        kept.append(room)
    return tuple(kept)


def _format_open_botc_lines(botc: BotcJoinSnapshot) -> list[str]:
    lines: list[str] = []
    if botc.phase:
        lines.append(f"**Fas:** {botc.phase}")
    if botc.storytellers:
        names = ", ".join(person.name for person in botc.storytellers)
        lines.append(f"**Storytellers:** {names}")
    filled = botc.filled_players
    total = botc.player_count if botc.player_count is not None else len(botc.players)
    if filled:
        names = ", ".join(person.name for person in filled)
        suffix = f" ({len(filled)}/{total})" if total else ""
        lines.append(f"**Spelare{suffix}:** {names}")
    elif total:
        lines.append(f"**Spelare:** 0/{total}")
    if botc.edition:
        lines.append(f"**Edition:** {botc.edition}")
    return lines


@dataclass(frozen=True)
class RsvpResponse:
    status: RsvpStatus
    role: RsvpRole = "Player"
    arrival_time: str | None = None


def status_from_emoji(emoji: str, emoji_to_status: Mapping[str, RsvpStatus]) -> RsvpStatus:
    return emoji_to_status.get(emoji, "Available")


def status_from_emojis(
    emojis: Iterable[str],
    emoji_to_status: Mapping[str, RsvpStatus],
) -> RsvpStatus | None:
    present = {status_from_emoji(emoji, emoji_to_status) for emoji in emojis}
    if not present:
        return None
    for status in REACTION_STATUS_PRECEDENCE:
        if status in present:
            return status
    return "Available"


def with_updated_response(
    responses: dict[int, RsvpResponse],
    *,
    user_id: int,
    status: RsvpStatus,
    role: RsvpRole,
    arrival_time: str | None = None,
) -> dict[int, RsvpResponse]:
    updated = dict(responses)
    updated[user_id] = RsvpResponse(status=status, role=role, arrival_time=arrival_time)
    return updated


def status_count(responses: dict[int, RsvpResponse], status: RsvpStatus) -> int:
    return len([r for r in responses.values() if r.status == status])


def mentions_for_status(responses: dict[int, RsvpResponse], status: RsvpStatus) -> str | None:
    ordered_responses = [
        (user_id, response)
        for user_id, response in responses.items()
        if response.status == status
    ]
    ordered_responses.sort(key=lambda pair: 0 if pair[1].role == "Storyteller" else 1)
    mentions: list[str] = []
    for user_id, response in ordered_responses:
        st_emoji = (response.role == "Storyteller" and f" {STORYTELLER_EMOJI}") or ""
        arrival = (response.arrival_time and f" ({response.arrival_time})") or ""
        mentions.append(f"{st_emoji}<@{user_id}>{arrival}")
    if not mentions:
        return None
    preview = ", ".join(mentions[:15])
    if len(mentions) > 15:
        preview += f" (+{len(mentions) - 15} more)"
    return preview


def apply_status_update(
    current: RsvpResponse,
    *,
    status: RsvpStatus,
    arrival_time: str | None = None,
) -> RsvpResponse:
    return RsvpResponse(
        status=status,
        role=("Player" if status == "Cant" else current.role),
        arrival_time=(
            None
            if status == "Cant"
            else (arrival_time if arrival_time is not None else current.arrival_time)
        ),
    )


def apply_storyteller_role(current: RsvpResponse, *, is_storyteller: bool) -> RsvpResponse:
    if is_storyteller:
        return RsvpResponse(
            status=((current.status != "Cant") and current.status) or "Available",
            role="Storyteller",
            arrival_time=None if current.status == "Cant" else current.arrival_time,
        )
    return RsvpResponse(
        status=current.status,
        role="Player",
        arrival_time=current.arrival_time,
    )


def apply_arrival_time(current: RsvpResponse, *, arrival_time: str) -> RsvpResponse:
    return RsvpResponse(
        status=("Available" if current.status == "Cant" else current.status),
        role=current.role,
        arrival_time=arrival_time,
    )
