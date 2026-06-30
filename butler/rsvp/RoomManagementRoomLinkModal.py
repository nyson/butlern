import butler.design
from butler.event_logic import normalize_room_url
from butler.rsvp.AvailabilityView import can_manage_room_action
from butler.rsvp.RoomManagementView import RoomManagementView
from butler.rsvp.AvailabilityView import announce_room_opening


import discord


from typing import ClassVar

from butler.settings_store import GuildSettingsStore


class RoomManagementRoomLinkModal(discord.ui.Modal, title=butler.design.ROOM_LINK_MODAL_TITLE):
    room_link: ClassVar[discord.ui.TextInput[RoomManagementRoomLinkModal]] = discord.ui.TextInput(
        label=butler.design.ROOM_LINK_MODAL_LABEL,
        placeholder=butler.design.ROOM_LINK_MODAL_PLACEHOLDER,
        max_length=butler.design.ROOM_LINK_MODAL_MAX_LENGTH,
    )

    def __init__(self, management_view: RoomManagementView, settings_store: GuildSettingsStore) -> None:
        super().__init__()
        self.management_view = management_view
        self.settings_store = settings_store

    def event_manager_role(self, interaction: discord.Interaction) -> int | None:
        return (interaction.guild
            and self.settings_store.get_event_manager_role_id(interaction.guild.id)
            or None)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not can_manage_room_action(
            interaction,
            event_manager_role_id=self.event_manager_role(interaction),
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
                butler.design.ROOM_LINK_MODAL_INVALID_MESSAGE,
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