from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from butler.rooms.constants import ROOM_CLOSED_PRUNE_SECONDS
from butler.rooms.types import BotcJoinSnapshot

RsvpRole = Literal["Player", "Storyteller"]
RsvpStatus = Literal["Available", "Maybe", "Cant"]
RoomState = Literal["pending", "open", "closed"]
RoomButton = Literal["open_or_prompt", "close", "close_all", "remove"]
RoomSource = Literal["manual", "polled"]

__all__ = [
    "ROOM_CLOSED_PRUNE_SECONDS",
    "RoomButton",
    "RoomEntry",
    "RoomSource",
    "RoomState",
    "RsvpRole",
    "RsvpStatus",
    "ViewState",
    "rooms_from_legacy",
]


@dataclass(frozen=True)
class RoomEntry:
    room_id: str
    url: str
    state: RoomState
    label: str | None = None
    source: RoomSource = "manual"
    last_checked_unix: int | None = None
    closed_at_unix: int | None = None
    # Wall clock when an open room last became empty (0 filled players + 0 STs).
    empty_since_unix: int | None = None
    botc: BotcJoinSnapshot | None = None
    # SHA-256 of track fields (state/phase/player count/edition); skip no-op edits.
    content_hash: str | None = None


@dataclass(frozen=True)
class ViewState:
    event_name: str
    start_unix: int
    event_url: str
    edition: str | None
    edition_emoji: str | None
    edition_image_url: str | None
    event_description: str
    rooms: tuple[RoomEntry, ...] = ()
    event_card_message_id: int | None = None
    room_state: RoomState = "pending"
    room_url: str | None = None

    def with_rooms(self, rooms: tuple[RoomEntry, ...]) -> ViewState:
        primary = _primary_room(rooms)
        if primary is None:
            mirrored_state: RoomState = "pending"
            mirrored_url = None
        elif primary.state == "open":
            mirrored_state = "open"
            mirrored_url = primary.url
        else:
            mirrored_state = primary.state
            mirrored_url = None if primary.state == "closed" else primary.url
        return ViewState(
            event_name=self.event_name,
            start_unix=self.start_unix,
            event_url=self.event_url,
            edition=self.edition,
            edition_emoji=self.edition_emoji,
            edition_image_url=self.edition_image_url,
            event_description=self.event_description,
            rooms=rooms,
            event_card_message_id=self.event_card_message_id,
            room_state=mirrored_state,
            room_url=mirrored_url,
        )


def _primary_room(rooms: tuple[RoomEntry, ...]) -> RoomEntry | None:
    for room in rooms:
        if room.state == "open":
            return room
    return rooms[0] if rooms else None


def rooms_from_legacy(*, room_state: RoomState, room_url: str | None) -> tuple[RoomEntry, ...]:
    if room_url is None:
        return ()
    state: RoomState = room_state if room_state in {"pending", "open", "closed"} else "open"
    return (
        RoomEntry(
            room_id="legacy",
            url=room_url,
            label=None,
            state=state,
            source="manual",
        ),
    )
