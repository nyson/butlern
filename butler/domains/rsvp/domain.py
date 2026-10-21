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

    entry = RoomEntry(
        room_id=new_room_id(normalized),
        url=normalized,
        label=label,
        state=state,
        source="polled" if is_botc_join_url(normalized) else "manual",
        last_checked_unix=stamp if botc is not None else None,
        closed_at_unix=stamp if state == "closed" else None,
        botc=botc if state == "open" and botc is not None else None,
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
            replace(
                room,
                state="closed",
                botc=None,
                closed_at_unix=room.closed_at_unix or stamp,
            )
        )
    return tuple(updated)


def close_all_open_rooms(
    rooms: Sequence[RoomEntry],
    *,
    now_unix: int | None = None,
) -> tuple[RoomEntry, ...]:
    """Close every open room; drop pending (never fully linked) rooms."""
    stamp = int(time.time() if now_unix is None else now_unix)
    updated: list[RoomEntry] = []
    for room in rooms:
        if room.state == "pending":
            continue
        if room.state != "open":
            updated.append(room)
            continue
        updated.append(
            replace(
                room,
                state="closed",
                botc=None,
                closed_at_unix=room.closed_at_unix or stamp,
            )
        )
    return tuple(updated)


def apply_botc_snapshot(
    room: RoomEntry,
    *,
    snapshot: BotcJoinSnapshot,
    now_unix: int,
) -> tuple[RoomEntry, bool]:
    """Return (updated_room, newly_opened)."""
    previous = room.state
    if snapshot.occupancy == "open":
        updated = replace(
            room,
            state="open",
            label=snapshot.code or room.label,
            last_checked_unix=now_unix,
            closed_at_unix=None,
            botc=snapshot,
            source="polled",
        )
        return updated, previous != "open"
    if snapshot.occupancy == "closed":
        updated = replace(
            room,
            state="closed",
            label=snapshot.code or room.label,
            last_checked_unix=now_unix,
            closed_at_unix=room.closed_at_unix or now_unix,
            botc=None,
            source="polled",
        )
        return updated, False
    return replace(room, last_checked_unix=now_unix), False


def room_display_signature(room: RoomEntry) -> tuple[object, ...]:
    """Fields that affect the Discord room section (text + controls), not poll metadata."""
    botc = room.botc
    if botc is None:
        botc_key: tuple[object, ...] = ()
    else:
        botc_key = (
            botc.occupancy,
            botc.edition,
            botc.phase,
            botc.player_count,
            tuple((person.name, person.is_empty_seat) for person in botc.storytellers),
            tuple((person.name, person.is_empty_seat) for person in botc.players),
        )
    return (room.room_id, room.url, room.state, room.label, botc_key)


def rooms_display_signature(rooms: Sequence[RoomEntry]) -> tuple[tuple[object, ...], ...]:
    return tuple(room_display_signature(room) for room in rooms)


def rooms_require_rerender(
    before: Sequence[RoomEntry],
    after: Sequence[RoomEntry],
) -> bool:
    """True when Discord room sections would look/behave differently."""
    return rooms_display_signature(before) != rooms_display_signature(after)


def prune_stale_closed_rooms(
    rooms: Sequence[RoomEntry],
    *,
    now_unix: int,
    max_closed_age: int = ROOM_CLOSED_PRUNE_SECONDS,
) -> tuple[RoomEntry, ...]:
    kept: list[RoomEntry] = []
    for room in rooms:
        if room.state != "closed" or room.closed_at_unix is None:
            kept.append(room)
            continue
        if now_unix - room.closed_at_unix >= max_closed_age:
            continue
        kept.append(room)
    return tuple(kept)


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
