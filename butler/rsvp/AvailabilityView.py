from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from typing import Final

import discord

from butler.design import (
    ARRIVE_LATER_BUTTON_LABEL,
    ARRIVE_LATER_EMOJI,
    AVAILABLE_BUTTON_LABEL,
    CANT_BUTTON_LABEL,
    MAYBE_BUTTON_LABEL,
    ROOM_CLOSE_BUTTON_EMOJI,
    ROOM_CLOSE_BUTTON_LABEL,
    ROOM_LINK_PROMPT_BUTTON_EMOJI,
    ROOM_LINK_PROMPT_BUTTON_LABEL,
    RSVP_STATUS_EMOJIS,
    STORYTELLER_BUTTON_LABEL,
    STORYTELLER_EMOJI,
)
from butler.rsvp.rsvp_domain import RoomSnapshot, RsvpResponse, visible_room_buttons
from butler.rsvp.rsvp_render import RsvpRenderState, render_rsvp_content
from butler.rsvp.rsvp_store import RsvpMessageStore
from butler.rsvp.types import RsvpStatus, ViewState
from butler.rsvp.view_helpers import can_manage_room_action, room_permission_denied_message
from butler.settings_store import GuildSettingsStore


class AvailabilityView(discord.ui.View):
    STATUSES: Final[tuple[tuple[RsvpStatus, str], ...]] = RSVP_STATUS_EMOJIS

    def __init__(
        self,
        *,
        view_state: ViewState,
        settings_store: GuildSettingsStore,
        view_store: RsvpMessageStore,
        message_id: int | None = None,
        channel_id: int | None = None,
        guild_id: int | None = None,
        timeout: float | None = None,
    ) -> None:
        super().__init__(timeout=timeout)
        self.view_state = view_state
        self.settings_store = settings_store
        self.view_store = view_store
        self.message_id = message_id
        self.channel_id = channel_id
        self.guild_id = guild_id
        self._ephemeral_responses: dict[int, RsvpResponse] = {}
        self._lock = asyncio.Lock()
        self._sync_room_action_buttons()

    def bind_message_context(
        self,
        *,
        message_id: int,
        channel_id: int,
        guild_id: int,
    ) -> None:
        self.message_id = message_id
        self.channel_id = channel_id
        self.guild_id = guild_id
        self.view_store.upsert_message(
            message_id=message_id,
            channel_id=channel_id,
            guild_id=guild_id,
            view_state=self.view_state,
        )
        if self._ephemeral_responses:
            for user_id, response in self._ephemeral_responses.items():
                self.view_store.upsert_rsvp_response(
                    message_id=message_id,
                    user_id=user_id,
                    response=response,
                )
            self._ephemeral_responses.clear()

    def _persist_view_state(self) -> None:
        if self.message_id is None or self.channel_id is None or self.guild_id is None:
            return
        self.view_store.upsert_message(
            message_id=self.message_id,
            channel_id=self.channel_id,
            guild_id=self.guild_id,
            view_state=self.view_state,
        )

    def event_manager_role(self, interaction: discord.Interaction) -> int | None:
        if interaction.guild is not None:
            return self.settings_store.get_event_manager_role_id(interaction.guild.id)
        if self.guild_id is not None:
            return self.settings_store.get_event_manager_role_id(self.guild_id)
        return None

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

    async def _all_responses(self) -> dict[int, RsvpResponse]:
        if self.message_id is None:
            return dict(self._ephemeral_responses)
        try:
            return self.view_store.all_responses(self.message_id)
        except OSError:
            return dict(self._ephemeral_responses)

    def _get_response_or_default(self, user_id: int) -> RsvpResponse:
        if self.message_id is None:
            return self._ephemeral_responses.get(user_id) or RsvpResponse(
                role="Player",
                status="Available",
                arrival_time=None,
            )
        try:
            existing = self.view_store.get_rsvp_response(self.message_id, user_id)
        except OSError:
            existing = self._ephemeral_responses.get(user_id)
        return existing or RsvpResponse(role="Player", status="Available", arrival_time=None)

    async def _update_response(self, user_id: int, response: RsvpResponse | None) -> None:
        if self.message_id is None:
            if response is None:
                self._ephemeral_responses.pop(user_id, None)
            else:
                self._ephemeral_responses[user_id] = response
            return
        try:
            if response is None:
                self.view_store.remove_rsvp_response(message_id=self.message_id, user_id=user_id)
                self._ephemeral_responses.pop(user_id, None)
                return
            self.view_store.upsert_rsvp_response(
                message_id=self.message_id,
                user_id=user_id,
                response=response,
            )
            self._ephemeral_responses.pop(user_id, None)
        except OSError:
            if response is None:
                self._ephemeral_responses.pop(user_id, None)
            else:
                self._ephemeral_responses[user_id] = response

    async def _render_state(self) -> RsvpRenderState:
        return RsvpRenderState(
            event_name=self.view_state.event_name,
            event_description=self.view_state.event_description,
            edition_emoji=self.view_state.edition_emoji,
            room_state=self.view_state.room_state,
            room_url=(
                self.view_state.room_state == "open" and self.view_state.room_url
            )
            or None,
            responses=await self._all_responses(),
        )

    async def with_response_or_default(
        self,
        user_id: int,
        fn: Callable[[RsvpResponse], RsvpResponse],
    ) -> None:
        async with self._lock:
            await self._update_response(user_id, fn(self._get_response_or_default(user_id)))

    async def build_content(self) -> str:
        return render_rsvp_content(await self._render_state())

    def build_embed(self) -> discord.Embed | None:
        return None

    def open_room_permission_denied_message(
        self,
        interaction: discord.Interaction,
    ) -> str:
        return room_permission_denied_message(
            interaction,
            event_manager_role_id=self.event_manager_role(interaction),
        )

    async def toggle_storyteller(
        self,
        *,
        user_id: int,
    ) -> None:
        current = self._get_response_or_default(user_id)
        await self.set_storyteller_role(
            user_id=user_id,
            is_storyteller=current.role != "Storyteller",
        )

    async def set_storyteller_role(
        self,
        *,
        user_id: int,
        is_storyteller: bool,
    ) -> None:
        await self.with_response_or_default(
            user_id,
            lambda current: RsvpResponse(
                role="Storyteller" if is_storyteller else "Player",
                status=(
                    ((current.status != "Cant") and current.status) or "Available"
                )
                if is_storyteller
                else current.status,
                arrival_time=None
                if is_storyteller and current.status == "Cant"
                else current.arrival_time,
            ),
        )

    async def set_user_response(
        self,
        *,
        user_id: int,
        status: RsvpStatus,
        arrival_time: str | None = None,
    ) -> None:
        await self.with_response_or_default(
            user_id,
            lambda current: RsvpResponse(
                role=current.role,
                status=status,
                arrival_time=(
                    None
                    if status == "Cant"
                    else (
                        arrival_time
                        if arrival_time is not None
                        else current.arrival_time
                    )
                ),
            ),
        )

    async def remove_user_response(self, user_id: int) -> None:
        async with self._lock:
            await self._update_response(user_id, None)

    async def set_room_url(self, room_url: str | None) -> None:
        async with self._lock:
            snapshot = RoomSnapshot.from_url(room_url)
            self.view_state = ViewState(
                event_name=self.view_state.event_name,
                start_unix=self.view_state.start_unix,
                event_url=self.view_state.event_url,
                edition=self.view_state.edition,
                edition_emoji=self.view_state.edition_emoji,
                room_state=snapshot.state,
                room_url=snapshot.url,
                edition_image_url=self.view_state.edition_image_url,
                event_description=self.view_state.event_description,
            )
            self._persist_view_state()
            self._sync_room_action_buttons()

    async def close_room(self) -> None:
        async with self._lock:
            snapshot = RoomSnapshot.closed()
            self.view_state = ViewState(
                event_name=self.view_state.event_name,
                start_unix=self.view_state.start_unix,
                event_url=self.view_state.event_url,
                edition=self.view_state.edition,
                edition_emoji=self.view_state.edition_emoji,
                room_state=snapshot.state,
                room_url=snapshot.url,
                edition_image_url=self.view_state.edition_image_url,
                event_description=self.view_state.event_description,
            )
            self._persist_view_state()
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
            return
        try:
            await interaction.message.edit(
                content=await self.build_content(),
                embed=self.build_embed(),
                view=self,
            )
        except discord.HTTPException:
            return

    async def _record_storyteller_toggle(
        self,
        interaction: discord.Interaction,
    ) -> None:
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
        await self.with_response_or_default(
            interaction.user.id,
            lambda current: RsvpResponse(
                role=(status == "Cant" and "Player") or current.role,
                status=status,
                arrival_time=(
                    None
                    if status == "Cant"
                    else (
                        arrival_time
                        if arrival_time is not None
                        else current.arrival_time
                    )
                ),
            ),
        )

        if interaction.message is None:
            return

        await self._remove_other_rsvp_reactions_for_user(
            message=interaction.message,
            user=interaction.user,
            selected_status=status,
        )
        await self.rebuild(interaction)

    @discord.ui.button(
        label=AVAILABLE_BUTTON_LABEL,
        style=discord.ButtonStyle.success,
        emoji=RSVP_STATUS_EMOJIS[0][1],
        custom_id="butler:rsvp:available",
        row=0,
    )
    async def available(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[AvailabilityView],
    ) -> None:
        await self._record_availability(interaction, "Available")

    @discord.ui.button(
        label=MAYBE_BUTTON_LABEL,
        style=discord.ButtonStyle.secondary,
        emoji=RSVP_STATUS_EMOJIS[1][1],
        custom_id="butler:rsvp:maybe",
        row=0,
    )
    async def maybe(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[AvailabilityView],
    ) -> None:
        await self._record_availability(interaction, "Maybe")

    @discord.ui.button(
        label=CANT_BUTTON_LABEL,
        style=discord.ButtonStyle.secondary,
        emoji=RSVP_STATUS_EMOJIS[2][1],
        custom_id="butler:rsvp:cant",
        row=0,
    )
    async def cant(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[AvailabilityView],
    ) -> None:
        await self._record_availability(interaction, "Cant")

    @discord.ui.button(
        label=ARRIVE_LATER_BUTTON_LABEL,
        style=discord.ButtonStyle.secondary,
        emoji=ARRIVE_LATER_EMOJI,
        custom_id="butler:rsvp:later",
        row=1,
    )
    async def later(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[AvailabilityView],
    ) -> None:
        from butler.rsvp.ArrivingLaterModal import ArrivingLaterModal

        await interaction.response.send_modal(ArrivingLaterModal(self))

    @discord.ui.button(
        label=STORYTELLER_BUTTON_LABEL,
        style=discord.ButtonStyle.secondary,
        emoji=STORYTELLER_EMOJI,
        custom_id="butler:rsvp:storyteller",
        row=1,
    )
    async def storyteller(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[AvailabilityView],
    ) -> None:
        await self._record_storyteller_toggle(interaction)

    @discord.ui.button(
        label=ROOM_LINK_PROMPT_BUTTON_LABEL,
        style=discord.ButtonStyle.secondary,
        emoji=ROOM_LINK_PROMPT_BUTTON_EMOJI,
        custom_id="butler:rsvp:open-room",
        row=2,
    )
    async def prompt_room_link(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[AvailabilityView],
    ) -> None:
        if not can_manage_room_action(
            interaction,
            event_manager_role_id=self.event_manager_role(interaction),
        ):
            await interaction.response.send_message(
                self.open_room_permission_denied_message(interaction),
                ephemeral=True,
            )
            return

        from butler.rsvp.RoomLinkModal import RoomLinkModal

        await interaction.response.send_modal(RoomLinkModal(self))

    @discord.ui.button(
        label=ROOM_CLOSE_BUTTON_LABEL,
        style=discord.ButtonStyle.danger,
        emoji=ROOM_CLOSE_BUTTON_EMOJI,
        custom_id="butler:rsvp:close-room",
        row=2,
    )
    async def close_room_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[AvailabilityView],
    ) -> None:
        if not can_manage_room_action(
            interaction,
            event_manager_role_id=self.event_manager_role(interaction),
        ):
            await interaction.response.send_message(
                self.open_room_permission_denied_message(interaction),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=False)
        await self.close_room()
        await self.rebuild(interaction)
