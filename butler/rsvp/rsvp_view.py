from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from contextlib import suppress
from typing import ClassVar, Final

import discord

from butler.design import (
    ARRIVE_LATER_BUTTON_LABEL,
    ARRIVE_LATER_EMOJI,
    ARRIVE_LATER_MODAL_TITLE,
    AVAILABLE_BUTTON_LABEL,
    CANT_BUTTON_LABEL,
    MAYBE_BUTTON_LABEL,
    ROOM_CLOSE_BUTTON_EMOJI,
    ROOM_CLOSE_BUTTON_LABEL,
    ROOM_LINK_MODAL_INVALID_MESSAGE,
    ROOM_LINK_MODAL_LABEL,
    ROOM_LINK_MODAL_MAX_LENGTH,
    ROOM_LINK_MODAL_PLACEHOLDER,
    ROOM_LINK_MODAL_TITLE,
    ROOM_LINK_PERMISSION_DENIED_MESSAGE,
    ROOM_LINK_PERMISSION_DENIED_ROLE_TEMPLATE,
    ROOM_LINK_PROMPT_BUTTON_EMOJI,
    ROOM_LINK_PROMPT_BUTTON_LABEL,
    ROOM_OPENED_NO_MENTIONS_TEMPLATE,
    ROOM_OPENED_WITH_MENTIONS_TEMPLATE,
    RSVP_STATUS_EMOJIS,
    STORYTELLER_BUTTON_LABEL,
    STORYTELLER_EMOJI,
)
from butler.event_logic import normalize_room_url
from butler.permissions import member_can_manage_events, permission_denied_message
from butler.rsvp.rsvp_domain import (
    RoomSnapshot,
    RsvpResponse,
    visible_room_buttons,
)
from butler.rsvp.rsvp_render import RsvpRenderState, render_rsvp_content, room_line
from butler.rsvp.types import RoomState, RsvpStatus, ViewState


def _build_user_mentions(user_ids: list[int]) -> str:
    unique_user_ids = sorted(set(user_ids))
    return " ".join(f"<@{user_id}>" for user_id in unique_user_ids)

def _can_manage_room_action(
    interaction: discord.Interaction,
    *,
    event_manager_role_id: int | None,
) -> bool:
    user = interaction.user
    if not isinstance(user, discord.Member):
        return False
    return member_can_manage_events(user, event_manager_role_id=event_manager_role_id)

def _room_permission_denied_message(
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
        without_role=ROOM_LINK_PERMISSION_DENIED_MESSAGE,
        with_role_template=ROOM_LINK_PERMISSION_DENIED_ROLE_TEMPLATE,
    )

async def _announce_room_opening(
    *,
    interaction: discord.Interaction,
    availability_view: AvailabilityView,
    message_link: str,
) -> None:
    statuses_to_ping: tuple[RsvpStatus, ...] = ("Available","Maybe")
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
        self.room_state: RoomState = RoomSnapshot.from_url(room_url).state
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
        visible = visible_room_buttons(self.room_state)
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



    async def _view_state(self) -> ViewState:
        return ViewState(
            event_name=self.event_name,
            start_unix=self.start_unix,
            event_url=self.event_url,
            edition=self.edition,
            edition_emoji=self.edition_emoji,
            edition_image_url=self.edition_image_url,
            event_manager_role_id=self.event_manager_role_id,
            event_description=self.event_description,
            room_state=self.room_state,
            room_url=self.room_url,
        )


    async def _render_state(self) -> RsvpRenderState:
        st = await self._view_state()

        return RsvpRenderState(
            event_name=st.event_name,
            event_description=st.event_description,
            edition_emoji=st.edition_emoji,
            room_state=st.room_state,
            room_url=(st.room_state == "open" and st.room_url) or None,
            responses=await self._all_responses(),
        )

    async def _all_responses(self) -> dict[int, RsvpResponse]:
        return self.responses

    def _get_response_or_default(self, user_id: int) -> RsvpResponse:
        return self.responses.get(user_id) \
            or RsvpResponse(role="Player", status="Available", arrival_time=None)

    async def _update_response(self, user_id: int, response: RsvpResponse | None) -> None:
        if response is None:
            if user_id in self.responses:
                updated = dict(self.responses)
                updated.pop(user_id)
                self.responses = updated
        else:
            self.responses[user_id] = response

    async def with_response_or_default(
            self,
            user_id: int,
            fn: Callable[[RsvpResponse], RsvpResponse]
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
        return _room_permission_denied_message(
            interaction,
            event_manager_role_id=self.event_manager_role_id,
        )

    async def toggle_storyteller(
        self,
        *,
        user_id: int,
    ) -> None:
        await self.with_response_or_default(user_id, lambda current: RsvpResponse(
            role="Player" if current.role == "Storyteller" else "Storyteller",
            status=(current and current.status) or "Available",
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
            role=current.role,
            status=status,
            arrival_time=arrival_time,
        ))

        if interaction.message is None:
            return

        await self._remove_other_rsvp_reactions_for_user(
            message=interaction.message,
            user=interaction.user,
            selected_status=status)

        await self.rebuild(interaction)

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
        await self._record_availability(interaction, "Available")

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
        await self._record_availability(interaction, "Maybe")

    @discord.ui.button(
        label=CANT_BUTTON_LABEL,
        style=discord.ButtonStyle.secondary,
        emoji=RSVP_STATUS_EMOJIS[2][1],
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
        row=1
    )
    async def later(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[AvailabilityView]
    ) -> None:
        await interaction.response.send_modal(ArrivingLaterModal(self))

    @discord.ui.button(
        label=STORYTELLER_BUTTON_LABEL,
        style=discord.ButtonStyle.secondary,
        emoji=STORYTELLER_EMOJI,
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
        row=2,
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
        await self.rebuild(interaction)

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
        visible = visible_room_buttons(self.availability_view.room_state)
        self._set_button_visibility(
            self.open_room_button,
            visible="open_or_prompt" in visible,
        )
        self._set_button_visibility(
            self.close_room_button,
            visible="close" in visible,
        )

    def _access_summary(self) -> str:
        if self.event_manager_role_id is None:
            return "`Hantera server`"
        return f"`Hantera server` eller <@&{self.event_manager_role_id}>"

    def build_content(self) -> str:
        line = room_line(
            room_state=self.availability_view.room_state,
            room_url=self.availability_view.room_url,
        )
        return (
            "## Rumshantering\n"
            f"Behörighet: {self._access_summary()}\n"
            f"{line or ''}"
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

class ArrivingLaterModal(discord.ui.Modal, title=ARRIVE_LATER_MODAL_TITLE):
    arriving_later_hours: ClassVar[discord.ui.TextInput[ArrivingLaterModal]] = discord.ui.TextInput(
        label=ARRIVE_LATER_MODAL_TITLE,
        placeholder="19:00",
        max_length=10
    )
    def __init__(self, view: AvailabilityView):
        super().__init__()
        self.view = view

    def _parse_time(self, time_str: str) -> str | None:
        parsed = None

        with contextlib.suppress(ValueError):
            parsed = time.strptime(time_str, "%H:%M")
        with contextlib.suppress(ValueError):
            parsed = time.strptime(time_str, "%H%M")

        if not parsed:
            return None
        return time.strftime("%H:%M", parsed)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        arrival_time = self._parse_time(self.arriving_later_hours.value)

        if arrival_time is None:
            await interaction.response.send_message(
                "Du måste skriva in tiden i formatet HH:MM, exempelvis 19:30!",
                ephemeral=True,
            )

        await self.view.set_user_response(
            user_id = interaction.user.id,
            status= "Available",
            arrival_time=arrival_time)

        if interaction.message is not None:
            await interaction.message.edit(
                content=await self.view.build_content(),
                embed=self.view.build_embed(),
                view=self.view)
            await interaction.response.defer(ephemeral=True, thinking=False)



class RoomLinkModal(discord.ui.Modal, title=ROOM_LINK_MODAL_TITLE):
    """Modal for opening rooms"""
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
            await self.view.rebuild(interaction)
        if interaction.message is None:
            print("Room-open announcement skipped: interaction message missing.")
            return

        statuses_to_ping: tuple[RsvpStatus, ...] = (
            "Available",
            "Maybe",
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
