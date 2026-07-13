from __future__ import annotations

import contextlib
import time
from dataclasses import replace
from typing import ClassVar

import discord

from butler.design import ARRIVE_LATER_MODAL_TITLE
from butler.rsvp.AvailabilityView import AvailabilityView

ARRIVE_LATER_DEFAULT = "19:00"

class ArrivingLaterModal(discord.ui.Modal, title=ARRIVE_LATER_MODAL_TITLE):
    arriving_later_hours: ClassVar[discord.ui.TextInput[ArrivingLaterModal]] = discord.ui.TextInput(
        label=ARRIVE_LATER_MODAL_TITLE,
        placeholder=ARRIVE_LATER_DEFAULT,
        max_length=10,
    )

    def __init__(self, view: AvailabilityView):
        super().__init__()
        self.view = view

    def _parse_time(self, time_str: str) -> str | None:
        parsed = None
        for fmt in ("%H:%M", "%H%M", "%H.%M", "%H %M"):
            with contextlib.suppress(ValueError):
                parsed = time.strptime(time_str, fmt)
                break
        return parsed and time.strftime("%H:%M", parsed)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        arrival_time = self._parse_time(self.arriving_later_hours.value)
        if arrival_time is None:
            await interaction.response.send_message(
                "Du måste skriva in tiden i formatet HH:MM, exempelvis 19:30!",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=False)
        await self.view.with_response_or_default(
            interaction.user.id,
            lambda current: replace(
                current,
                status=(current.status == "Cant" and "Available") \
                    or current.status,
                arrival_time=arrival_time,
            )
        )
        if interaction.message is not None:
            await self.view.rebuild(interaction)
