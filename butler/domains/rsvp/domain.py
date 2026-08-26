from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final

from butler.design import STORYTELLER_EMOJI
from butler.domains.rsvp.types import RoomButton, RoomState, RsvpRole, RsvpStatus

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


def visible_room_buttons(room_state: RoomState) -> frozenset[RoomButton]:
    if room_state == "open":
        return frozenset({"close"})
    return frozenset({"open_or_prompt"})


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
