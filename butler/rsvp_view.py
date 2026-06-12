from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import ClassVar, Final

import discord

from butler.design import (
    ARRIVE_LATER_BUTTON_LABEL,
    AVAILABLE_BUTTON_LABEL,
    EVENT_POST_TEMPLATE,
    MAYBE_BUTTON_LABEL,
    ROOM_LINK_MODAL_INVALID_MESSAGE,
    ROOM_LINK_MODAL_LABEL,
    ROOM_LINK_MODAL_MAX_LENGTH,
    ROOM_LINK_MODAL_PLACEHOLDER,
    ROOM_LINK_MODAL_TITLE,
    ROOM_LINK_PERMISSION_DENIED_MESSAGE,
    ROOM_LINK_PROMPT_BUTTON_EMOJI,
    ROOM_LINK_PROMPT_BUTTON_LABEL,
    ROOM_OPENED_NO_MENTIONS_TEMPLATE,
    ROOM_OPENED_WITH_MENTIONS_TEMPLATE,
    RSVP_FOOTER_TEXT,
    RSVP_STATUS_EMOJIS,
    RSVP_STATUS_LABELS,
    STORYTELLER_BUTTON_LABEL,
    STORYTELLER_EMOJI,
)
from butler.event_logic import normalize_room_url
from butler.rsvp_domain import (
    RsvpResponse,
    RsvpStatus,
    mentions_for_status,
    status_count,
    with_updated_response,
)


def _build_user_mentions(user_ids: list[int]) -> str:
    unique_user_ids = sorted(set(user_ids))
    return " ".join(f"<@{user_id}>" for user_id in unique_user_ids)

def _can_open_room_action(
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
        self._lock = asyncio.Lock()
        self._sync_link_buttons()

    def _sync_link_buttons(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.style == discord.ButtonStyle.link:
                self.remove_item(child)

    def _build_room_section(self) -> str:
        if self.room_url is None:
            return ""
        return (
            f"**Rummet är öppet:** {self.room_url}\n"
            "Använd knapparna nedan för att svara.\n\n"
        )

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
            mentions = mentions_for_status(self.responses, status)
            sections.append(f"{emoji} **{display_label} ({count})**\n{mentions}")
        return "\n\n".join(sections)

    def build_content(self) -> str:
        title_line = self._build_title_line()
        return EVENT_POST_TEMPLATE.format(
            title_line=title_line,
            event_description=self.event_description,
            room_section=self._build_room_section(),
            status_sections=self._build_status_sections(),
            footer_text=RSVP_FOOTER_TEXT,
        )

    def build_embed(self) -> discord.Embed | None:
        if self.edition_image_url is None:
            return None
        embed = discord.Embed()
        embed.set_image(url=self.edition_image_url)
        return embed

    def _open_room_permission_denied_message(
        self,
        interaction: discord.Interaction,
    ) -> str:
        if self.event_manager_role_id is None:
            return ROOM_LINK_PERMISSION_DENIED_MESSAGE
        guild = interaction.guild
        if guild is None:
            return ROOM_LINK_PERMISSION_DENIED_MESSAGE
        role = guild.get_role(self.event_manager_role_id)
        if role is None:
            return ROOM_LINK_PERMISSION_DENIED_MESSAGE
        return (
            "Du behöver behörigheten `Hantera server` eller rollen "
            f"{role.mention} för att öppna rum."
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
            self._sync_link_buttons()

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
        if not _can_open_room_action(
            interaction,
            event_manager_role_id=self.event_manager_role_id,
        ):
            await interaction.response.send_message(
                self._open_room_permission_denied_message(interaction),
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(RoomLinkModal(self))


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
        if not _can_open_room_action(
            interaction,
            event_manager_role_id=self.view.event_manager_role_id,
        ):
            await interaction.response.send_message(
                self.view._open_room_permission_denied_message(interaction),
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
