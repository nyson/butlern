from butler.design import ROOM_CLOSE_BUTTON_EMOJI, ROOM_CLOSE_BUTTON_LABEL, ROOM_LINK_PROMPT_BUTTON_EMOJI, ROOM_LINK_PROMPT_BUTTON_LABEL
from butler.rsvp.AvailabilityView import AvailabilityView, can_manage_room_action, room_permission_denied_message
from butler.rsvp.rsvp_domain import visible_room_buttons
from butler.rsvp.rsvp_render import room_line
from butler.rsvp.RoomManagementRoomLinkModal import RoomManagementRoomLinkModal


import discord

class RoomManagementView(discord.ui.View):
    def __init__(
        self,
        *,
        availability_view: AvailabilityView,
        timeout: float | None = None,
    ) -> None:
        super().__init__(timeout=timeout)
        self.availability_view = availability_view
        self._rsvp_message: discord.Message | None = None
        self._sync_room_action_buttons()

    def bind_rsvp_message(self, rsvp_message: discord.Message) -> None:
        self._rsvp_message = rsvp_message

    def _set_button_visibility(
        self,
        button: discord.ui.Button[RoomManagementView],
        *,
        visible: bool,
    ) -> None:
        if visible and button not in self.children:
            self.add_item(button)
            return
        if not visible and button in self.children:
            self.remove_item(button)

    def _sync_room_action_buttons(self) -> None:
        visible = visible_room_buttons(self.availability_view.view_state.room_state)
        self._set_button_visibility(
            self.open_room_button,
            visible="open_or_prompt" in visible,
        )
        self._set_button_visibility(
            self.close_room_button,
            visible="close" in visible,
        )

    def _access_summary(self, interaction: discord.Interaction) -> str:
        event_manager_role_id = self.availability_view.event_manager_role(interaction)
        if event_manager_role_id is None:
            return "`Hantera server`"
        return f"`Hantera server` eller <@&{event_manager_role_id}>"

    def build_content(self, interaction: discord.Interaction) -> str:
        line = room_line(
            room_state=self.availability_view.view_state.room_state,
            room_url=self.availability_view.room_url,
        )
        return (
            "## Rumshantering\n"
            f"Behörighet: {self._access_summary(interaction)}\n"
            f"{line or ''}"
        )

    def permission_denied_message(self, interaction: discord.Interaction) -> str:
        return room_permission_denied_message(
            interaction,
            event_manager_role_id=self.availability_view.event_manager_role(interaction)    ,
        )

    async def _refresh_rsvp_message(self) -> None:
        if self._rsvp_message is None:
            return
        try:
            await self._rsvp_message.edit(
                content=await self.availability_view.build_content(),
                embed=self.availability_view.build_embed(),
                view=self.availability_view,
            )
        except discord.HTTPException:
            return

    async def _refresh_management_message(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if interaction.message is None:
            return
        try:
            await interaction.message.edit(
                content=self.build_content(interaction),
                view=self,
            )
        except discord.HTTPException:
            return

    async def refresh_messages(self, interaction: discord.Interaction) -> None:
        self._sync_room_action_buttons()
        await self._refresh_rsvp_message()
        await self._refresh_management_message(interaction)

    def resolve_rsvp_message_link(self, interaction: discord.Interaction) -> str:
        if self._rsvp_message is not None:
            return self._rsvp_message.jump_url
        if interaction.message is not None:
            return interaction.message.jump_url
        return self.availability_view.view_state.event_url

    @discord.ui.button(
        label=ROOM_LINK_PROMPT_BUTTON_LABEL,
        style=discord.ButtonStyle.secondary,
        emoji=ROOM_LINK_PROMPT_BUTTON_EMOJI,
        row=0,
    )
    async def open_room_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[RoomManagementView],
    ) -> None:
        if not can_manage_room_action(
            interaction,
            event_manager_role_id=self.availability_view.event_manager_role(interaction),
        ):
            await interaction.response.send_message(
                self.permission_denied_message(interaction),
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(RoomManagementRoomLinkModal(self, settings_store=self.availability_view.settings_store))

    @discord.ui.button(
        label=ROOM_CLOSE_BUTTON_LABEL,
        style=discord.ButtonStyle.danger,
        emoji=ROOM_CLOSE_BUTTON_EMOJI,
        row=0,
    )
    async def close_room_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[RoomManagementView],
    ) -> None:
        if not can_manage_room_action(
            interaction,
            event_manager_role_id=self.availability_view.event_manager_role(interaction),
        ):
            await interaction.response.send_message(
                self.permission_denied_message(interaction),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=False)
        await self.availability_view.close_room()
        await self.refresh_messages(interaction)