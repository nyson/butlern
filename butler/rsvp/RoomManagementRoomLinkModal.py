from __future__ import annotations

from typing import ClassVar

import discord

from butler.design import (
    ROOM_LINK_MODAL_INVALID_MESSAGE,
    ROOM_LINK_MODAL_LABEL,
    ROOM_LINK_MODAL_MAX_LENGTH,
    ROOM_LINK_MODAL_PLACEHOLDER,
    ROOM_LINK_MODAL_TITLE,
)
from butler.event_logic import normalize_room_url
from butler.rsvp.RoomManagementView import RoomManagementView
from butler.rsvp.view_helpers import announce_room_opening, can_manage_room_action


class RoomManagementRoomLinkModal(discord.ui.Modal, title=ROOM_LINK_MODAL_TITLE):
    room_link: ClassVar[
        discord.ui.TextInput[RoomManagementRoomLinkModal]
    ] = discord.ui.TextInput(
        label=ROOM_LINK_MODAL_LABEL,
        placeholder=ROOM_LINK_MODAL_PLACEHOLDER,
        max_length=ROOM_LINK_MODAL_MAX_LENGTH,
    )

    def __init__(self, management_view: RoomManagementView) -> None:
        super().__init__()
        self.management_view = management_view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not can_manage_room_action(
            interaction,
            event_manager_role_id=self.management_view.availability_view.event_manager_role(
                interaction
            ),
        ):
            await interaction.response.send_message(
                self.management_view.permission_denied_message(interaction),
                ephemeral=True,
            )
            return

        raw_room_link = self.room_link.value.strip()
        try:
            normalized_room_url = normalize_room_url(raw_room_link)
        except ValueError:
            normalized_room_url = None

        if normalized_room_url is None:
            await interaction.response.send_message(
                ROOM_LINK_MODAL_INVALID_MESSAGE,
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=False)
        await self.management_view.availability_view.set_room_url(normalized_room_url)
        await self.management_view.refresh_messages(interaction)
        await announce_room_opening(
            interaction=interaction,
            availability_view=self.management_view.availability_view,
            message_link=self.management_view.resolve_rsvp_message_link(interaction),
        )
