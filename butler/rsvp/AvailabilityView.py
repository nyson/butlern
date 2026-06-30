from ctypes import ArgumentError

from discord import user

import butler.design
from butler.permissions import member_can_manage_events, permission_denied_message
from butler.rsvp.ArrivingLaterModal import ArrivingLaterModal
from butler.rsvp.RoomLinkModal import RoomLinkModal
from butler.rsvp.rsvp_domain import RoomSnapshot, visible_room_buttons
from butler.rsvp.rsvp_render import RsvpRenderState, render_rsvp_content
from butler.rsvp.rsvp_store import RsvpMessageStore
from butler.rsvp.types import RsvpResponse, RsvpStatus, ViewState


import discord


import asyncio
from collections.abc import Callable
from contextlib import suppress
from typing import Final

from butler.settings_store import GuildSettingsStore


def can_manage_room_action(
    interaction: discord.Interaction,
    *,
    event_manager_role_id: int | None,
) -> bool:
    user = interaction.user
    if not isinstance(user, discord.Member):
        return False
    return member_can_manage_events(user, event_manager_role_id=event_manager_role_id)


def room_permission_denied_message(
    interaction: discord.Interaction,
    *,
    event_manager_role_id: int | None,
) -> str:
    guild = interaction.guild
    role = (
        guild.get_role(event_manager_role_id)
        if guild is not None and event_manager_role_id is not None
        else None
    )
    return permission_denied_message(
        role_mention=role.mention if role is not None else None,
        without_role=butler.design.ROOM_LINK_PERMISSION_DENIED_MESSAGE,
        with_role_template=butler.design.ROOM_LINK_PERMISSION_DENIED_ROLE_TEMPLATE,
    )


class AvailabilityView(discord.ui.View):
    STATUSES: Final[tuple[tuple[RsvpStatus, str], ...]] = butler.design.RSVP_STATUS_EMOJIS

    def __init__(
        self,
        *,
        view_state: ViewState,
        settings_store: GuildSettingsStore,
        view_store: RsvpMessageStore,
        timeout: float | None = None,
    ) -> None:
        super().__init__(timeout=timeout)
        self.view_state = view_state
        self._lock = asyncio.Lock()
        # self.responses: dict[int, RsvpResponse] = {}
        self._sync_room_action_buttons()
        self.settings_store = settings_store
        self.view_store = view_store

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
        visible = visible_room_buttons(self.view_state.room_state)
        self._set_button_visibility(
            self.prompt_room_link,
            visible="open_or_prompt" in visible,
        )
        self._set_button_visibility(
            self.close_room_button,
            visible="close" in visible,
        )

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

    async def _render_state(self, interaction: discord.Interaction) -> RsvpRenderState:
        message = interaction.message
        if not message:
            raise ArgumentError(f"_render_state; Expected a message!")

        return RsvpRenderState(
            event_name=self.view_state.event_name,
            event_description=self.view_state.event_description,
            edition_emoji=self.view_state.edition_emoji,
            room_state=self.view_state.room_state,
            room_url=(self.view_state.room_state == "open" and self.view_state.room_url) or None,
            responses=await self._all_responses(message.id),
        )

    async def _all_responses(self, message_id: int) -> dict[int, RsvpResponse]:
        d = { r.user: r for r in self.view_store.all_responses(message_id)}
        return d

    def _default_rspv_response(self, user_id: int) -> RsvpResponse:
        return RsvpResponse(
            user=user_id,
            status="Available",
            role="Player",
            arrival_time=None)

    def _get_response_or_default(self, message_id: int, user_id: int) -> RsvpResponse:
        return self.view_store.get_rsvp_response(message_id, user_id) \
            or self.view_store.add_rsvp_response(
                message_id, 
                self._default_rspv_response(user_id))
        

    async def with_response_or_default(
            self,
            user_id: int,
            fn: Callable[[RsvpResponse], RsvpResponse]
        ) -> None:
        async with self._lock:
            await self._update_response(user_id, fn(self._get_response_or_default(user_id)))

    async def build_content(self, interaction: discord.Interaction) -> str:
        return render_rsvp_content(await self._render_state(interaction))

    def build_embed(self) -> discord.Embed | None:
        return None

    def open_room_permission_denied_message(
        self,
        interaction: discord.Interaction,
    ) -> str:
        return room_permission_denied_message(
            interaction,
            event_manager_role_id=interaction.guild
                and self.settings_store.get_event_manager_role_id(interaction.guild.id)
                or None)

    async def toggle_storyteller(
        self,
        *,
        user_id: int,
    ) -> None:
        await self.with_response_or_default(user_id, lambda current: RsvpResponse(
            role="Player" if current.role == "Storyteller" else "Storyteller",
            status=(current and current.status != "Cant" and current.status)\
                or "Available",
            arrival_time=(current and current.arrival_time) or None,
        ))

    async def set_user_response(
        self,
        *,
        user_id: int,
        status: RsvpStatus,
        arrival_time: str | None = None,
    ) -> None:
        await self.with_response_or_default(user_id, lambda current: RsvpResponse(
            role=current.role,
            status=status,
            arrival_time=arrival_time,
        ))

    async def remove_user_response(self, user_id: int) -> None:
        await self._update_response(user_id, None)

    async def set_room_url(self, room_url: str | None) -> None:
        async with self._lock:
            snapshot = RoomSnapshot.from_url(room_url)
            self.room_url = snapshot.url
            self.room_state = snapshot.state
            self._sync_room_action_buttons()

    async def close_room(self) -> None:
        async with self._lock:
            snapshot = RoomSnapshot.closed()
            self.room_url = snapshot.url
            self.room_state = snapshot.state
            self._sync_room_action_buttons()

    async def get_user_ids_for_status(self, status: RsvpStatus) -> list[int]:
        async with self._lock:
            return [
                user_id
                for user_id, response in (await self._all_responses()).items()
                if response.status == status
            ]
    async def rebuild(self, interaction: discord.Interaction) -> None:
        if interaction.message is None:
            print("no interaction")
            return

        try:
            await interaction.message.edit(
                content=await self.build_content(),
                embed=self.build_embed(),
                view=self,
            )
        except discord.HTTPException:
            return

    def event_manager_role(self, interaction: discord.Interaction) -> int | None:
        return (interaction.guild
            and self.settings_store.get_event_manager_role_id(interaction.guild.id)
            or None)

    async def _record_storyteller_toggle(
            self,
            interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=False)
        await self.toggle_storyteller(user_id=interaction.user.id)
        await self.rebuild(interaction)

    async def _record_availability(
        self,
        interaction: discord.Interaction,
        status: RsvpStatus,
        *,
        arrival_time: str | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=False)
        await self.with_response_or_default(interaction.user.id, lambda current: RsvpResponse(
            role=(status == "Cant" and "Player") or current.role,
            status=status,
            arrival_time=(status == "Cant" and None) or arrival_time,
        ))

        if interaction.message is None:
            return

        await self._remove_other_rsvp_reactions_for_user(
            message=interaction.message,
            user=interaction.user,
            selected_status=status)

        await self.rebuild(interaction)

    @discord.ui.button(
        label=butler.design.AVAILABLE_BUTTON_LABEL,
        style=discord.ButtonStyle.success,
        emoji=butler.design.RSVP_STATUS_EMOJIS[0][1],
        row=0,
    )
    async def available(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[AvailabilityView],
    ) -> None:
        await self._record_availability(interaction, "Available")

    @discord.ui.button(
        label=butler.design.MAYBE_BUTTON_LABEL,
        style=discord.ButtonStyle.secondary,
        emoji=butler.design.RSVP_STATUS_EMOJIS[1][1],
        row=0,
    )
    async def maybe(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[AvailabilityView],
    ) -> None:
        await self._record_availability(interaction, "Maybe")

    @discord.ui.button(
        label=butler.design.CANT_BUTTON_LABEL,
        style=discord.ButtonStyle.secondary,
        emoji=butler.design.RSVP_STATUS_EMOJIS[2][1],
        row=0,
    )
    async def cant(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[AvailabilityView],
    ) -> None:
        await self._record_availability(interaction, "Cant")

    @discord.ui.button(
        label=butler.design.ARRIVE_LATER_BUTTON_LABEL,
        style=discord.ButtonStyle.secondary,
        emoji=butler.design.ARRIVE_LATER_EMOJI,
        row=1
    )
    async def later(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[AvailabilityView]
    ) -> None:
        await interaction.response.send_modal(ArrivingLaterModal(self))

    @discord.ui.button(
        label=butler.design.STORYTELLER_BUTTON_LABEL,
        style=discord.ButtonStyle.secondary,
        emoji=butler.design.STORYTELLER_EMOJI,
        row=1,
    )
    async def storyteller(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[AvailabilityView],
    ) -> None:
        await self._record_storyteller_toggle(interaction)

    @discord.ui.button(
        label=butler.design.ROOM_LINK_PROMPT_BUTTON_LABEL,
        style=discord.ButtonStyle.secondary,
        emoji=butler.design.ROOM_LINK_PROMPT_BUTTON_EMOJI,
        row=2,
    )
    async def prompt_room_link(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[AvailabilityView],
    ) -> None:
        if not can_manage_room_action(
            interaction,
            event_manager_role_id=interaction.guild
                and self.settings_store.get_event_manager_role_id(interaction.guild.id)
                or None,
        ):
            await interaction.response.send_message(
                self.open_room_permission_denied_message(interaction),
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(RoomLinkModal(self, self.settings_store))

    @discord.ui.button(
        label=butler.design.ROOM_CLOSE_BUTTON_LABEL,
        style=discord.ButtonStyle.danger,
        emoji=butler.design.ROOM_CLOSE_BUTTON_EMOJI,
        row=1,
    )
    async def close_room_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[AvailabilityView],
    ) -> None:
        if not can_manage_room_action(
            interaction,
            event_manager_role_id=interaction.guild
                and self.settings_store.get_event_manager_role_id(interaction.guild.id)
                or None,
        ):
            await interaction.response.send_message(
                self.open_room_permission_denied_message(interaction),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=False)
        await self.close_room()
        await self.rebuild(interaction)


def build_user_mentions(user_ids: list[int]) -> str:
    unique_user_ids = sorted(set(user_ids))
    return " ".join(f"<@{user_id}>" for user_id in unique_user_ids)


async def announce_room_opening(
    *,
    interaction: discord.Interaction,
    availability_view: AvailabilityView,
    message_link: str,
) -> None:
    statuses_to_ping: tuple[RsvpStatus, ...] = ("Available","Maybe")
    user_ids_to_ping: list[int] = []
    for status in statuses_to_ping:
        user_ids_to_ping.extend(await availability_view.get_user_ids_for_status(status))
    mentions = build_user_mentions(user_ids_to_ping)

    channel = interaction.channel
    if isinstance(channel, (discord.TextChannel, discord.Thread)):
        if mentions:
            content = butler.design.ROOM_OPENED_WITH_MENTIONS_TEMPLATE.format(
                mentions=mentions,
                message_link=message_link,
            )
        else:
            content = butler.design.ROOM_OPENED_NO_MENTIONS_TEMPLATE.format(
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