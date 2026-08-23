from __future__ import annotations

import discord
from discord.ui import TextDisplay

from butler.design import (
    EVENT_POST_TEMPLATE,
    ROOM_CLOSED_MESSAGE,
    ROOM_OPENED_MESSAGE_TEMPLATE,
    RSVP_FOOTER_TEXT,
    RSVP_STATUS_EMOJIS,
    RSVP_STATUS_LABELS,
)
from butler.domains.rsvp.domain import RsvpResponse, mentions_for_status, status_count
from butler.domains.rsvp.types import RoomState
from butler.rsvp.snapshot import RsvpRenderSnapshot

__all__ = [
    "RsvpBodyDisplay",
    "RsvpRenderSnapshot",
    "format_rsvp_body",
    "is_discord_scheduled_event_url",
]


def is_discord_scheduled_event_url(event_url: str) -> bool:
    """True for guild scheduled-event URLs (not channel placeholder links)."""
    return "/events/" in event_url


def _room_line(*, room_state: RoomState, room_url: str | None) -> str | None:
    if room_state == "open" and room_url is not None:
        return ROOM_OPENED_MESSAGE_TEMPLATE.format(room_url=room_url)
    if room_state == "closed":
        return ROOM_CLOSED_MESSAGE
    return None


def _room_section(*, room_state: RoomState, room_url: str | None) -> str | None:
    line = _room_line(room_state=room_state, room_url=room_url)
    if line is None:
        return None
    return f"{line}\n\n"


def _title_line(*, event_name: str, edition_emoji: str | None) -> str:
    if edition_emoji is None:
        return event_name
    return f"{edition_emoji} {event_name}"


def _status_sections(responses: dict[int, RsvpResponse]) -> str:
    sections: list[str] = []
    for status, emoji in RSVP_STATUS_EMOJIS:
        count = status_count(responses, status)
        display_label = RSVP_STATUS_LABELS[status]
        mentions = mentions_for_status(responses, status) or ""
        sections.append(f"{emoji}  **{display_label} ({count})**\n{mentions}")
    return "\n\n".join(sections)


def format_rsvp_body(snapshot: RsvpRenderSnapshot) -> str:
    room_section = _room_section(
        room_state=snapshot.room_state,
        room_url=snapshot.room_url,
    )
    return EVENT_POST_TEMPLATE.format(
        title_line=_title_line(
            event_name=snapshot.event_name,
            edition_emoji=snapshot.edition_emoji,
        ),
        event_description=snapshot.event_description,
        event_section="",
        room_section=room_section if room_section is not None else "\n",
        status_sections=_status_sections(snapshot.responses),
        footer_text=RSVP_FOOTER_TEXT,
    )


class RsvpBodyDisplay(TextDisplay[discord.ui.LayoutView]):
    """LayoutView text block built from an RSVP render snapshot."""

    def __init__(self, snapshot: RsvpRenderSnapshot) -> None:
        super().__init__(format_rsvp_body(snapshot))
