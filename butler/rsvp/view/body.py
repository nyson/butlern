from __future__ import annotations

import discord
from discord import ui
from discord.ui import TextDisplay

from butler.design import (
    EVENT_POST_TEMPLATE,
    ROOM_CLOSED_MESSAGE,
    ROOM_OPENED_MESSAGE_TEMPLATE,
    RSVP_EMPTY_PLACEHOLDER,
    RSVP_STATUS_EMOJIS,
    RSVP_STATUS_LABELS,
)
from butler.domains.rsvp.domain import RsvpResponse, mentions_for_status, status_count
from butler.domains.rsvp.types import RoomState
from butler.rsvp.snapshot import RsvpRenderSnapshot

__all__ = [
    "RsvpBodyDisplay",
    "RsvpRenderSnapshot",
    "build_rsvp_body_item",
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
    """Render non-empty status lists; placeholder when every list is empty."""
    sections: list[str] = []
    for status, emoji in RSVP_STATUS_EMOJIS:
        count = status_count(responses, status)
        if count == 0:
            continue
        display_label = RSVP_STATUS_LABELS[status]
        mentions = mentions_for_status(responses, status) or ""
        sections.append(f"{emoji}  **{display_label} ({count})**\n{mentions}")
    if not sections:
        return RSVP_EMPTY_PLACEHOLDER
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
        status_sections=_status_sections(snapshot.responses)
    )


class RsvpBodyDisplay(TextDisplay[discord.ui.LayoutView]):
    """LayoutView text block built from an RSVP render snapshot."""

    def __init__(self, snapshot: RsvpRenderSnapshot) -> None:
        super().__init__(format_rsvp_body(snapshot))


def build_rsvp_body_item(
    snapshot: RsvpRenderSnapshot,
) -> ui.Item[discord.ui.LayoutView]:
    """Body text, with edition logo thumbnail when `edition_image_url` is set."""
    body = RsvpBodyDisplay(snapshot)
    image_url = snapshot.edition_image_url
    if image_url is None or not image_url.strip():
        return body
    # Section accessory shows the edition icon beside the RSVP body so changing
    # edition media is visible on re-render (not only the title emoji).
    return ui.Section(body, accessory=ui.Thumbnail(media=image_url))
