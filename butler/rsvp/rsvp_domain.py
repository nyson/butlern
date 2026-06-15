from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final, Literal

RsvpStatus = Literal["Available", "Maybe", "Later", "Storyteller"]

# Order in which reaction-derived statuses win when a user holds several reactions
# at once: Maybe > Later > Storyteller > Available.
REACTION_STATUS_PRECEDENCE: Final[tuple[RsvpStatus, ...]] = (
    "Maybe",
    "Later",
    "Storyteller",
    "Available",
)


@dataclass(frozen=True)
class RsvpResponse:
    status: RsvpStatus
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
    for status in REACTION_STATUS_PRECEDENCE:
        if status in present:
            return status
    return None


def with_updated_response(
    responses: dict[int, RsvpResponse],
    *,
    user_id: int,
    status: RsvpStatus,
    arrival_time: str | None = None,
) -> dict[int, RsvpResponse]:
    updated = dict(responses)
    updated[user_id] = RsvpResponse(status=status, arrival_time=arrival_time)
    return updated


def status_count(responses: dict[int, RsvpResponse], status: RsvpStatus) -> int:
    return sum(1 for response in responses.values() if response.status == status)


def mentions_for_status(responses: dict[int, RsvpResponse], status: RsvpStatus) -> str | None:
    mentions: list[str] = []
    for user_id, response in responses.items():
        if response.status != status:
            continue
        if status == "Later" and response.arrival_time:
            mentions.append(f"<@{user_id}> ({response.arrival_time})")
        else:
            mentions.append(f"<@{user_id}>")

    if not mentions:
        return None

    preview = ", ".join(mentions[:15])
    if len(mentions) > 15:
        preview += f" (+{len(mentions) - 15} more)"
    return preview
