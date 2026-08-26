from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import ui

from butler.design import SELECT_EVENT_BUTTON_EMOJI, SELECT_EVENT_BUTTON_LABEL

if TYPE_CHECKING:
    from butler.rsvp.view.event_message_view import EventMessageView


class EventCardView(ui.View):
    """Companion message above the RSVP: native event URL/placeholder + link button."""

    def __init__(self, *, rsvp_view: EventMessageView) -> None:
        super().__init__(timeout=None)
        self.rsvp_view = rsvp_view

    @ui.button(
        label=SELECT_EVENT_BUTTON_LABEL,
        emoji=SELECT_EVENT_BUTTON_EMOJI,
        style=discord.ButtonStyle.success,
        custom_id="butler:rsvp:select-event",
    )
    async def select_event(
        self,
        interaction: discord.Interaction,
        _button: ui.Button[EventCardView],
    ) -> None:
        await self.rsvp_view.open_event_select(interaction)
