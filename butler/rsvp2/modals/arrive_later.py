from __future__ import annotations

import contextlib
import time
from collections.abc import Awaitable, Callable
from typing import ClassVar

import discord

from butler.design import (
    ARRIVE_LATER_MODAL_PLACEHOLDER,
    ARRIVE_LATER_MODAL_TITLE,
)

SubmitArrival = Callable[[discord.Interaction, str], Awaitable[None]]


def parse_arrival_time(time_str: str) -> str | None:
    parsed = None
    for fmt in ("%H:%M", "%H%M", "%H.%M", "%H %M"):
        with contextlib.suppress(ValueError):
            parsed = time.strptime(time_str, fmt)
            break
    return parsed and time.strftime("%H:%M", parsed)


class ArriveLaterModal(discord.ui.Modal, title=ARRIVE_LATER_MODAL_TITLE):
    arriving_later_hours: ClassVar[discord.ui.TextInput[ArriveLaterModal]] = discord.ui.TextInput(
        label=ARRIVE_LATER_MODAL_TITLE,
        placeholder=ARRIVE_LATER_MODAL_PLACEHOLDER,
        max_length=10,
    )

    def __init__(self, *, on_submit: SubmitArrival) -> None:
        super().__init__()
        self._on_submit = on_submit

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._on_submit(interaction, self.arriving_later_hours.value)
