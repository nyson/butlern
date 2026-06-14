from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import ClassVar, Final, Literal

import discord

from butler.design import (
    ARRIVE_LATER_BUTTON_LABEL,
    AVAILABLE_BUTTON_LABEL,
    EVENT_POST_TEMPLATE,
    MAYBE_BUTTON_LABEL,
    ROOM_CLOSE_BUTTON_EMOJI,
    ROOM_CLOSE_BUTTON_LABEL,
    ROOM_CLOSED_MESSAGE,
    ROOM_LINK_MODAL_INVALID_MESSAGE,
    ROOM_LINK_MODAL_LABEL,
    ROOM_LINK_MODAL_MAX_LENGTH,
    ROOM_LINK_MODAL_PLACEHOLDER,
    ROOM_LINK_MODAL_TITLE,
    ROOM_LINK_PERMISSION_DENIED_MESSAGE,
    ROOM_LINK_PERMISSION_DENIED_ROLE_TEMPLATE,
    ROOM_LINK_PROMPT_BUTTON_EMOJI,
    ROOM_LINK_PROMPT_BUTTON_LABEL,
    ROOM_OPENED_MESSAGE_TEMPLATE,
    ROOM_OPENED_NO_MENTIONS_TEMPLATE,
    ROOM_OPENED_WITH_MENTIONS_TEMPLATE,
    RSVP_FOOTER_TEXT,
    RSVP_STATUS_EMOJIS,
    RSVP_STATUS_LABELS,
    STORYTELLER_BUTTON_LABEL,
    STORYTELLER_EMOJI,
)
from butler.event_logic import normalize_room_url
from butler.rsvp.rsvp_domain import (
    RsvpResponse,
    RsvpStatus,
    mentions_for_status,
    status_count,
    with_updated_response,
)

RoomState = Literal["pending", "open", "closed"]


def _build_user_mentions(user_ids: list[int]) -> str:
    unique_user_ids = sorted(set(user_ids))
    return " ".join(f"<@{user_id}>" for user_id in unique_user_ids)

def _can_manage_room_action(
    interaction: discord.Interaction,
    *,
    event_manager_role_id: int | None,
) -> bool:
    if not isinstance(interaction.user, discord.Member):
        return False
    if interaction.user.guild_permissions.manage_guild:
        return True
    if event_manager_role_id is None:
        return False
    return any(role.id == event_manager_role_id for role in interaction.user.roles)

def _room_permission_denied_message(
    interaction: discord.Interaction,
    *,
    event_manager_role_id: int | None,
) -> str:
    guild = interaction.guild
    role_id = event_manager_role_id
    role = guild and role_id and guild.get_role(role_id) or None
    if role:
        return ROOM_LINK_PERMISSION_DENIED_ROLE_TEMPLATE.format(mention=role.mention)
    return ROOM_LINK_PERMISSION_DENIED_MESSAGE

async def _announce_room_opening(
    *,
    interaction: discord.Interaction,
    availability_view: AvailabilityView,
    message_link: str,
) -> None:
    statuses_to_ping: tuple[RsvpStatus, ...] = (
        "Available",
        "Maybe",
        "Later",
        "Storyteller",
    )
    user_ids_to_ping: list[int] = []
    for status in statuses_to_ping:
        user_ids_to_ping.extend(await availability_view.get_user_ids_for_status(status))
    mentions = _build_user_mentions(user_ids_to_ping)

    channel = interaction.channel
    if isinstance(channel, (discord.TextChannel, discord.Thread)):
        if mentions:
            content = ROOM_OPENED_WITH_MENTIONS_TEMPLATE.format(
                mentions=mentions,
                message_link=message_link,
            )
        else:
            content = ROOM_OPENED_NO_MENTIONS_TEMPLATE.format(
                message_link=message_link,
            )
        with suppress(discord.HTTPException):
            await channel.send(
                content,
                allowed_mentions=discord.AllowedMentions(
                    users=True,
                    roles=False,
                    everyone=False,
                ),
            )


class AvailabilityView(discord.ui.View):
    STATUSES: Final[tuple[tuple[RsvpStatus, str], ...]] = RSVP_STATUS_EMOJIS

    def __init__(
        self,
        *,
        event_name: str,
        start_unix: int,
        event_url: str,
        room_url: str | None,
        edition: str | None,
        edition_emoji: str | None,
        edition_image_url: str | None,
        event_manager_role_id: int | None,
        event_description: str,
        timeout: float | None = None,
    ) -> None:
        super().__init__(timeout=timeout)
        self.event_name = event_name
        self.start_unix = start_unix
        self.event_url = event_url
        self.room_url = room_url
        self.edition = edition
        self.edition_emoji = edition_emoji
        self.edition_image_url = edition_image_url
        self.event_manager_role_id = event_manager_role_id
        self.event_description = event_description
        self.responses: dict[int, RsvpResponse] = {}
        self.room_state: RoomState = "open" if room_url is not None else "pending"
        self._lock = asyncio.Lock()
        self._sync_room_action_buttons()

    def _set_button_visibility(
        self,
        button: discord.ui.Button[AvailabilityView],
        *,
        visible: bool,
    ) -> None:
        if visible and button not in self.children:
            self.add_item(button)
            return
        if not visible and button in self.children:
            self.remove_item(button)

    def _sync_room_action_buttons(self) -> None:
        self._set_button_visibility(
            self.prompt_room_link,
            visible=self.room_state in {"pending", "closed"},
        )
        self._set_button_visibility(
            self.close_room_button,
            visible=self.room_state == "open",
        )

    def _build_room_section(self) -> str | None:
        if self.room_state == "open" and self.room_url is not None:
            return (
                f"{ROOM_OPENED_MESSAGE_TEMPLATE.format(room_url=self.room_url)}"
                "\n\n"
            )
        elif self.room_state == "closed":
            return f"{ROOM_CLOSED_MESSAGE}\n\n"
        return None

    def _build_title_line(self) -> str:
        if self.edition_emoji is None:
            return self.event_name
        return f"{self.edition_emoji} {self.event_name}"
    def _emoji_for_status(self, status: RsvpStatus) -> str | None:
        for mapped_status, emoji in self.STATUSES:
            if mapped_status == status:
                return emoji
        return None

    async def _remove_other_rsvp_reactions_for_user(
        self,
        *,
        message: discord.Message,
        user: discord.abc.Snowflake,
        selected_status: RsvpStatus,
    ) -> None:
        selected_emoji = self._emoji_for_status(selected_status)
        if selected_emoji is None:
            return
        for _, emoji in self.STATUSES:
            if emoji == selected_emoji:
                continue
            with suppress(discord.HTTPException):
                await message.remove_reaction(emoji, user)

    def _build_status_sections(self) -> str:
        sections: list[str] = []
        for status, emoji in self.STATUSES:
            count = status_count(self.responses, status)
            display_label = RSVP_STATUS_LABELS[status]
            mentions = mentions_for_status(self.responses, status) or ""
            sections.append(f"{emoji} **{display_label} ({count})**\n{mentions}")
        return "\n\n".join(sections)

    def build_content(self) -> str:
        title_line = self._build_title_line()
        return EVENT_POST_TEMPLATE.format(
            title_line=title_line,
            event_description=self.event_description,
            room_section=self._build_room_section() or "\n",
            status_sections=self._build_status_sections(),
            footer_text=RSVP_FOOTER_TEXT,
        )

    def build_embed(self) -> discord.Embed | None:
        return None

    def open_room_permission_denied_message(
        self,
        interaction: discord.Interaction,
    ) -> str:
        return _room_permission_denied_message(
            interaction,
            event_manager_role_id=self.event_manager_role_id,
        )

    async def set_user_response(
        self,
        *,
        user_id: int,
        status: RsvpStatus,
        arrival_time: str | None = None,
    ) -> None:
        async with self._lock:
            self.responses = with_updated_response(
                self.responses,
                user_id=user_id,
                status=status,
                arrival_time=arrival_time,
            )

    async def remove_user_response(self, user_id: int) -> None:
        async with self._lock:
            if user_id in self.responses:
                updated = dict(self.responses)
                updated.pop(user_id)
                self.responses = updated

    async def set_room_url(self, room_url: str | None) -> None:
        async with self._lock:
            self.room_url = room_url
            self.room_state = "open" if room_url is not None else "pending"
            self._sync_room_action_buttons()

    async def close_room(self) -> None:
        async with self._lock:
            self.room_url = None
            self.room_state = "closed"
            self._sync_room_action_buttons()

    async def get_user_ids_for_status(self, status: RsvpStatus) -> list[int]:
        async with self._lock:
            return [
                user_id
                for user_id, response in self.responses.items()
                if response.status == status
            ]


    async def _record_interaction_response(
        self,
        interaction: discord.Interaction,
        status: RsvpStatus,
        *,
        arrival_time: str | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=False)
        await self.set_user_response(
            user_id=interaction.user.id,
            status=status,
            arrival_time=arrival_time,
        )
        if interaction.message is None:
            return
        await self._remove_other_rsvp_reactions_for_user(
            message=interaction.message,
            user=interaction.user,
            selected_status=status,
        )
        try:
            await interaction.message.edit(
                content=self.build_content(),
                embed=self.build_embed(),
                view=self,
            )
        except discord.HTTPException:
            return

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return True

    @discord.ui.button(
        label=AVAILABLE_BUTTON_LABEL,
        style=discord.ButtonStyle.success,
        emoji=RSVP_STATUS_EMOJIS[0][1],
        row=0,
    )
    async def available(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[AvailabilityView],
    ) -> None:
        await self._record_interaction_response(interaction, "Available")

    @discord.ui.button(
        label=MAYBE_BUTTON_LABEL,
        style=discord.ButtonStyle.secondary,
        emoji=RSVP_STATUS_EMOJIS[1][1],
        row=0,
    )
    async def maybe(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[AvailabilityView],
    ) -> None:
        await self._record_interaction_response(interaction, "Maybe")

    @discord.ui.button(
        label=ARRIVE_LATER_BUTTON_LABEL,
        style=discord.ButtonStyle.primary,
        emoji=RSVP_STATUS_EMOJIS[2][1],
        row=0,
    )
    async def arrive_later(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[AvailabilityView],
    ) -> None:
        await self._record_interaction_response(interaction, "Later")

    @discord.ui.button(
        label=STORYTELLER_BUTTON_LABEL,
        style=discord.ButtonStyle.secondary,
        emoji=STORYTELLER_EMOJI,
        row=0,
    )
    async def storyteller(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[AvailabilityView],
    ) -> None:
        await self._record_interaction_response(interaction, "Storyteller")

    @discord.ui.button(
        label=ROOM_LINK_PROMPT_BUTTON_LABEL,
        style=discord.ButtonStyle.secondary,
        emoji=ROOM_LINK_PROMPT_BUTTON_EMOJI,
        row=1,
    )
    async def prompt_room_link(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[AvailabilityView],
    ) -> None:
        if not _can_manage_room_action(
            interaction,
            event_manager_role_id=self.event_manager_role_id,
        ):
            await interaction.response.send_message(
                self.open_room_permission_denied_message(interaction),
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(RoomLinkModal(self))

    @discord.ui.button(
        label=ROOM_CLOSE_BUTTON_LABEL,
        style=discord.ButtonStyle.danger,
        emoji=ROOM_CLOSE_BUTTON_EMOJI,
        row=1,
    )
    async def close_room_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[AvailabilityView],
    ) -> None:
        if not _can_manage_room_action(
            interaction,
            event_manager_role_id=self.event_manager_role_id,
        ):
            await interaction.response.send_message(
                self.open_room_permission_denied_message(interaction),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=False)
        await self.close_room()
        if interaction.message is None:
            return
        try:
            await interaction.message.edit(
                content=self.build_content(),
                embed=self.build_embed(),
                view=self,
            )
        except discord.HTTPException:
            return


class RoomManagementView(discord.ui.View):
    def __init__(
        self,
        *,
        availability_view: AvailabilityView,
        timeout: float | None = None,
    ) -> None:
        super().__init__(timeout=timeout)
        self.availability_view = availability_view
        self.event_manager_role_id = availability_view.event_manager_role_id
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
        room_state = self.availability_view.room_state
        self._set_button_visibility(
            self.open_room_button,
            visible=room_state in {"pending", "closed"},
        )
        self._set_button_visibility(
            self.close_room_button,
            visible=room_state == "open",
        )

    def _access_summary(self) -> str:
        if self.event_manager_role_id is None:
            return "`Hantera server`"
        return f"`Hantera server` eller <@&{self.event_manager_role_id}>"

    def build_content(self) -> str:
        room_line = ""
        if (
            self.availability_view.room_state == "open"
            and self.availability_view.room_url is not None
        ):
            room_line = ROOM_OPENED_MESSAGE_TEMPLATE.format(
                room_url=self.availability_view.room_url)
        elif self.availability_view.room_state == "closed":
            room_line = ROOM_CLOSED_MESSAGE
        return (
            "## Rumshantering\n"
            f"Behörighet: {self._access_summary()}\n"
            f"{room_line}"
        )

    def permission_denied_message(self, interaction: discord.Interaction) -> str:
        return _room_permission_denied_message(
            interaction,
            event_manager_role_id=self.event_manager_role_id,
        )

    async def _refresh_rsvp_message(self) -> None:
        if self._rsvp_message is None:
            return
        try:
            await self._rsvp_message.edit(
                content=self.availability_view.build_content(),
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
                content=self.build_content(),
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
        return self.availability_view.event_url

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
        if not _can_manage_room_action(
            interaction,
            event_manager_role_id=self.event_manager_role_id,
        ):
            await interaction.response.send_message(
                self.permission_denied_message(interaction),
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(RoomManagementRoomLinkModal(self))

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
        if not _can_manage_room_action(
            interaction,
            event_manager_role_id=self.event_manager_role_id,
        ):
            await interaction.response.send_message(
                self.permission_denied_message(interaction),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=False)
        await self.availability_view.close_room()
        await self.refresh_messages(interaction)


class RoomManagementRoomLinkModal(discord.ui.Modal, title=ROOM_LINK_MODAL_TITLE):
    room_link: ClassVar[discord.ui.TextInput[RoomManagementRoomLinkModal]] = discord.ui.TextInput(
        label=ROOM_LINK_MODAL_LABEL,
        placeholder=ROOM_LINK_MODAL_PLACEHOLDER,
        max_length=ROOM_LINK_MODAL_MAX_LENGTH,
    )

    def __init__(self, management_view: RoomManagementView) -> None:
        super().__init__()
        self.management_view = management_view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not _can_manage_room_action(
            interaction,
            event_manager_role_id=self.management_view.event_manager_role_id,
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
        await _announce_room_opening(
            interaction=interaction,
            availability_view=self.management_view.availability_view,
            message_link=self.management_view.resolve_rsvp_message_link(interaction),
        )


class RoomLinkModal(discord.ui.Modal, title=ROOM_LINK_MODAL_TITLE):
    room_link: ClassVar[discord.ui.TextInput[RoomLinkModal]] = discord.ui.TextInput(
        label=ROOM_LINK_MODAL_LABEL,
        placeholder=ROOM_LINK_MODAL_PLACEHOLDER,
        max_length=ROOM_LINK_MODAL_MAX_LENGTH,
    )

    def __init__(self, view: AvailabilityView) -> None:
        super().__init__()
        self.view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not _can_manage_room_action(
            interaction,
            event_manager_role_id=self.view.event_manager_role_id,
        ):
            await interaction.response.send_message(
                self.view.open_room_permission_denied_message(interaction),
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
        await self.view.set_room_url(normalized_room_url)

        if interaction.message is not None:
            try:
                await interaction.message.edit(
                    content=self.view.build_content(),
                    embed=self.view.build_embed(),
                    view=self.view,
                )
            except discord.HTTPException:
                print("Failed to update RSVP message after room-link modal submit.")
                return
        if interaction.message is None:
            print("Room-open announcement skipped: interaction message missing.")
            return

        statuses_to_ping: tuple[RsvpStatus, ...] = (
            "Available",
            "Maybe",
            "Later",
            "Storyteller",
        )
        user_ids_to_ping: list[int] = []
        for status in statuses_to_ping:
            user_ids_to_ping.extend(await self.view.get_user_ids_for_status(status))
        mentions = _build_user_mentions(user_ids_to_ping)
        message_link = interaction.message.jump_url

        channel = interaction.channel
        if isinstance(channel, (discord.TextChannel, discord.Thread)):
            if mentions:
                content = ROOM_OPENED_WITH_MENTIONS_TEMPLATE.format(
                    mentions=mentions,
                    message_link=message_link,
                )
            else:
                content = ROOM_OPENED_NO_MENTIONS_TEMPLATE.format(
                    message_link=message_link,
                )

            with suppress(discord.HTTPException):
                await channel.send(
                    content,
                    allowed_mentions=discord.AllowedMentions(
                        users=True,
                        roles=False,
                        everyone=False,
                    ),
                )
        print("Room link updated from RSVP modal.")
