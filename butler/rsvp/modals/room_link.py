from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import ClassVar

import discord

from butler.design import (
    ROOM_LINK_MODAL_LABEL,
    ROOM_LINK_MODAL_MAX_LENGTH,
    ROOM_LINK_MODAL_PLACEHOLDER,
    ROOM_LINK_MODAL_TITLE,
)

SubmitRoomLink = Callable[[discord.Interaction, str], Awaitable[None]]


class RoomLinkModal(discord.ui.Modal, title=ROOM_LINK_MODAL_TITLE):
    room_link: ClassVar[discord.ui.TextInput[RoomLinkModal]] = discord.ui.TextInput(
        label=ROOM_LINK_MODAL_LABEL,
        placeholder=ROOM_LINK_MODAL_PLACEHOLDER,
        max_length=ROOM_LINK_MODAL_MAX_LENGTH,
    )

    def __init__(self, *, on_submit: SubmitRoomLink) -> None:
        super().__init__()
        self._on_submit = on_submit

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._on_submit(interaction, self.room_link.value)
