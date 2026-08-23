from __future__ import annotations

from collections.abc import Awaitable, Callable

import discord
from discord import ui

from butler.design import (
    SELECT_EVENT_MODAL_LABEL,
    SELECT_EVENT_MODAL_TITLE,
    SELECT_EVENT_PLACEHOLDER,
)

SubmitSelectedEvent = Callable[[discord.Interaction, str], Awaitable[None]]


class SelectEventModal(ui.Modal, title=SELECT_EVENT_MODAL_TITLE):
    """Storyteller modal: pick an existing Discord event from the warm cache."""

    def __init__(
        self,
        *,
        on_submit: SubmitSelectedEvent,
        options: list[discord.SelectOption],
    ) -> None:
        super().__init__()
        self._on_submit = on_submit
        self._event_select: ui.Select[SelectEventModal] = ui.Select(
            placeholder=SELECT_EVENT_PLACEHOLDER,
            min_values=1,
            max_values=1,
            options=options,
            custom_id="butler:rsvp2:select-event-modal-menu",
        )
        self.add_item(
            ui.Label(
                text=SELECT_EVENT_MODAL_LABEL,
                component=self._event_select,
            )
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        values = self._event_select.values
        if not values:
            await interaction.response.send_message(
                "Inget event valdes.",
                ephemeral=True,
            )
            return
        await self._on_submit(interaction, values[0])
