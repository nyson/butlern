from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final

from butler.design import STORYTELLER_EMOJI
from butler.rsvp.types import RoomButton, RoomState, RsvpRole, RsvpStatus

# Order in which reaction-derived statuses win when a user holds several reactions
# at once: Maybe > Cant > Available. Storyteller is tracked as a role, not a status.
REACTION_STATUS_PRECEDENCE: Final[tuple[RsvpStatus, ...]] = (
    "Maybe",
    "Cant",
    "Available",
)


@dataclass(frozen=True)
class RoomSnapshot:
    """Immutable result of a room-lifecycle transition: the state and its URL."""

    state: RoomState
    url: str | None

    @classmethod
    def from_url(cls, room_url: str | None) -> RoomSnapshot:
        """Setting a URL opens the room; clearing it reverts to `pending`."""
        if room_url is None:
            return cls(state="pending", url=None)
        return cls(state="open", url=room_url)

    @classmethod
    def closed(cls) -> RoomSnapshot:
        return cls(state="closed", url=None)


def visible_room_buttons(room_state: RoomState) -> frozenset[RoomButton]:
    """Which room-action buttons should be visible for a given room state.

    `open`: only the close button. `pending`/`closed`: only the open/prompt button.
    """
    if room_state == "open":
        return frozenset({"close"})
    return frozenset({"open_or_prompt"})


@dataclass(frozen=True)
class RsvpResponse:
    status: RsvpStatus
    role: RsvpRole = "Player"
    arrival_time: str | None = None


def status_from_emoji(emoji: str, emoji_to_status: Mapping[str, RsvpStatus]) -> RsvpStatus:
    """Map a single reaction emoji to a status. Unrecognized emojis count as `Available`."""
    return emoji_to_status.get(emoji, "Available")


def status_from_emojis(
    emojis: Iterable[str],
    emoji_to_status: Mapping[str, RsvpStatus],
) -> RsvpStatus | None:
    """Resolve the effective status for a user holding `emojis`, applying precedence.

    Returns `None` when no emojis are present (the user has no relevant reactions left).
    """
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
    mentions: list[str] = []
    for user_id, response in responses.items():
        if response.status != status:
            continue

        st_emoji = (response.role == "Storyteller" and f" {STORYTELLER_EMOJI}") or ""
        arrival = (response.arrival_time and f" ({response.arrival_time})") or ""
        mentions.append(f"{st_emoji}<@{user_id}>{arrival}")

    if not mentions:
        return None

    preview = ", ".join(mentions[:15])
    if len(mentions) > 15:
        preview += f" (+{len(mentions) - 15} more)"
    return preview
