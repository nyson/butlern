from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RsvpStatus = Literal["Available", "Maybe", "Later", "Storyteller"]


@dataclass(frozen=True)
class RsvpResponse:
    status: RsvpStatus
    arrival_time: str | None = None


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


def mentions_for_status(responses: dict[int, RsvpResponse], status: RsvpStatus) -> str:
    mentions: list[str] = []
    for user_id, response in responses.items():
        if response.status != status:
            continue
        if status == "Later" and response.arrival_time:
            mentions.append(f"<@{user_id}> ({response.arrival_time})")
        else:
            mentions.append(f"<@{user_id}>")

    if not mentions:
        return "—"

    preview = ", ".join(mentions[:15])
    if len(mentions) > 15:
        preview += f" (+{len(mentions) - 15} more)"
    return preview
