"""Pure rendering of RSVP message content.

This is the functional core for what the bot *posts*: given an immutable snapshot of
RSVP state (`RsvpRenderState`), produce the message string — with no Discord objects and
no side effects. The views in `rsvp_view.py` snapshot themselves into `RsvpRenderState`
and delegate here, so rendering can be tested without constructing a `discord.ui.View`.

Message templates and copy stay in `design.py`.
"""

from __future__ import annotations

from dataclasses import dataclass

import butler.design

from butler.rsvp.rsvp_domain import (
    RsvpResponse,
    mentions_for_status,
    status_count,
)
from butler.rsvp.types import RoomState


@dataclass(frozen=True)
class RsvpRenderState:
    """Immutable snapshot of everything needed to render an RSVP post."""

    event_name: str
    event_description: str
    edition_emoji: str | None
    room_state: RoomState
    room_url: str | None
    responses: dict[int, RsvpResponse]


def room_line(*, room_state: RoomState, room_url: str | None) -> str | None:
    """The single room-status line, or `None` when there is nothing to show.

    Shared by the RSVP post (which wraps it in spacing) and the room-management message.
    """
    if room_state == "open" and room_url is not None:
        return butler.design.ROOM_OPENED_MESSAGE_TEMPLATE.format(room_url=room_url)
    if room_state == "closed":
        return butler.design.ROOM_CLOSED_MESSAGE
    return None


def room_section(*, room_state: RoomState, room_url: str | None) -> str | None:
    """The room line as it appears in the RSVP post body (trailing spacing), or `None`."""
    line = room_line(room_state=room_state, room_url=room_url)
    if line is None:
        return None
    return f"{line}\n\n"


def title_line(*, event_name: str, edition_emoji: str | None) -> str:
    if edition_emoji is None:
        return event_name
    return f"{edition_emoji} {event_name}"


def status_sections(responses: dict[int, RsvpResponse]) -> str:
    sections: list[str] = []
    for status, emoji in butler.design.RSVP_STATUS_EMOJIS:
        count = status_count(responses, status)
        display_label = butler.design.RSVP_STATUS_LABELS[status]
        mentions = mentions_for_status(responses, status) or ""
        sections.append(f"{emoji}  **{display_label} ({count})**\n{mentions}")
    return "\n\n".join(sections)


def render_rsvp_content(state: RsvpRenderState) -> str:
    section = room_section(room_state=state.room_state, room_url=state.room_url)
    return butler.design.EVENT_POST_TEMPLATE.format(
        title_line=title_line(event_name=state.event_name, edition_emoji=state.edition_emoji),
        event_description=state.event_description,
        room_section=section if section is not None else "\n",
        status_sections=status_sections(state.responses),
        footer_text=butler.design.RSVP_FOOTER_TEXT,
    )
