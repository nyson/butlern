from __future__ import annotations

import logging
from contextlib import suppress
from dataclasses import replace
from typing import TypeAlias

import discord
from discord import ui

from butler.caches.events import AUTOCOMPLETE_EVENT_CACHE, resolve_existing_event_for_command
from butler.design import (
    ARRIVE_LATER_BUTTON_LABEL,
    ARRIVE_LATER_EMOJI,
    ARRIVE_LATER_INVALID_TIME_MESSAGE,
    AVAILABLE_BUTTON_LABEL,
    AVAILABLE_EMOJI,
    CANT_BUTTON_LABEL,
    CANT_EMOJI,
    MAYBE_BUTTON_LABEL,
    MAYBE_EMOJI,
    ROOM_CLOSE_BUTTON_EMOJI,
    ROOM_CLOSE_BUTTON_LABEL,
    ROOM_LINK_MODAL_INVALID_MESSAGE,
    ROOM_LINK_PROMPT_BUTTON_EMOJI,
    ROOM_LINK_PROMPT_BUTTON_LABEL,
    SELECT_EVENT_BUTTON_EMOJI,
    SELECT_EVENT_BUTTON_LABEL,
    SELECT_EVENT_EMPTY_MESSAGE,
    STORYTELLER_BUTTON_LABEL,
    STORYTELLER_EMOJI,
)
from butler.discord_events import build_event_url, event_start_unix
from butler.domains.result import Err, Ok, ServiceError
from butler.domains.rsvp.domain import RsvpResponse, visible_room_buttons
from butler.domains.rsvp.types import RsvpStatus, ViewState
from butler.event_logic import normalize_room_url
from butler.permissions import can_manage_room_action, room_permission_denied_message
from butler.rsvp.controller import RsvpController
from butler.rsvp.modals.arrive_later import ArriveLaterModal, parse_arrival_time
from butler.rsvp.modals.room_link import RoomLinkModal
from butler.rsvp.modals.select_event import SelectEventModal
from butler.rsvp.room_announce import announce_room_opening
from butler.rsvp.snapshot import RsvpRenderSnapshot
from butler.rsvp.view.body import RsvpBodyDisplay, is_discord_scheduled_event_url
from butler.rsvp.view.event_select import build_event_select_options
from butler.settings_store import GuildSettingsStore

logger = logging.getLogger(__name__)

_GUILD_REQUIRED_MESSAGE = "This command must be used in a server."
_GENERIC_SERVICE_ERROR_MESSAGE = "Något gick fel. Försök igen om en stund."
_LINK_EDIT_FAILED_MESSAGE = (
    "Kunde länka eventet internt, men misslyckades att uppdatera RSVP-meddelandet."
)

# Channels accepted by discord.PartialMessage(channel=...).
EditableChannel: TypeAlias = (
    discord.TextChannel
    | discord.VoiceChannel
    | discord.StageChannel
    | discord.Thread
    | discord.DMChannel
    | discord.PartialMessageable
    | discord.GroupChannel
)
_EDITABLE_CHANNEL_TYPES = (
    discord.TextChannel,
    discord.VoiceChannel,
    discord.StageChannel,
    discord.Thread,
    discord.DMChannel,
    discord.PartialMessageable,
    discord.GroupChannel,
)


class RsvpStatusActions(ui.ActionRow["EventMessageView"]):
    def __init__(self, root: EventMessageView) -> None:
        super().__init__()
        self.root = root

    @ui.button(
        label=AVAILABLE_BUTTON_LABEL,
        emoji=AVAILABLE_EMOJI,
        style=discord.ButtonStyle.success,
        custom_id="butler:rsvp:available",
    )
    async def available(
        self,
        interaction: discord.Interaction,
        _button: ui.Button[EventMessageView],
    ) -> None:
        await self.root.set_status(interaction, "Available")

    @ui.button(
        label=MAYBE_BUTTON_LABEL,
        emoji=MAYBE_EMOJI,
        style=discord.ButtonStyle.secondary,
        custom_id="butler:rsvp:maybe",
    )
    async def maybe(
        self,
        interaction: discord.Interaction,
        _button: ui.Button[EventMessageView],
    ) -> None:
        await self.root.set_status(interaction, "Maybe")

    @ui.button(
        label=CANT_BUTTON_LABEL,
        emoji=CANT_EMOJI,
        style=discord.ButtonStyle.secondary,
        custom_id="butler:rsvp:cant",
    )
    async def cant(
        self,
        interaction: discord.Interaction,
        _button: ui.Button[EventMessageView],
    ) -> None:
        await self.root.set_status(interaction, "Cant")


class RsvpMetaActions(ui.ActionRow["EventMessageView"]):
    def __init__(self, root: EventMessageView) -> None:
        super().__init__()
        self.root = root

    @ui.button(
        label=ARRIVE_LATER_BUTTON_LABEL,
        emoji=ARRIVE_LATER_EMOJI,
        style=discord.ButtonStyle.secondary,
        custom_id="butler:rsvp:later",
    )
    async def later(
        self,
        interaction: discord.Interaction,
        _button: ui.Button[EventMessageView],
    ) -> None:
        await self.root.open_arrive_later_modal(interaction)

    @ui.button(
        label=STORYTELLER_BUTTON_LABEL,
        emoji=STORYTELLER_EMOJI,
        style=discord.ButtonStyle.secondary,
        custom_id="butler:rsvp:storyteller",
    )
    async def storyteller(
        self,
        interaction: discord.Interaction,
        _button: ui.Button[EventMessageView],
    ) -> None:
        await self.root.toggle_storyteller(interaction)


class RoomActions(ui.ActionRow["EventMessageView"]):
    """Storyteller row: link Discord event + open/close room."""

    def __init__(self, root: EventMessageView) -> None:
        super().__init__()
        self.root = root
        visible = visible_room_buttons(root.view_state.room_state)
        if "open_or_prompt" not in visible:
            self.remove_item(self.open_room)
        if "close" not in visible:
            self.remove_item(self.close_room)

    @ui.button(
        label=SELECT_EVENT_BUTTON_LABEL,
        emoji=SELECT_EVENT_BUTTON_EMOJI,
        style=discord.ButtonStyle.secondary,
        custom_id="butler:rsvp:select-event",
    )
    async def select_event(
        self,
        interaction: discord.Interaction,
        _button: ui.Button[EventMessageView],
    ) -> None:
        await self.root.open_event_select(interaction)

    @ui.button(
        label=ROOM_LINK_PROMPT_BUTTON_LABEL,
        emoji=ROOM_LINK_PROMPT_BUTTON_EMOJI,
        style=discord.ButtonStyle.secondary,
        custom_id="butler:rsvp:open-room",
    )
    async def open_room(
        self,
        interaction: discord.Interaction,
        _button: ui.Button[EventMessageView],
    ) -> None:
        await self.root.open_room_modal(interaction)

    @ui.button(
        label=ROOM_CLOSE_BUTTON_LABEL,
        emoji=ROOM_CLOSE_BUTTON_EMOJI,
        style=discord.ButtonStyle.danger,
        custom_id="butler:rsvp:close-room",
    )
    async def close_room(
        self,
        interaction: discord.Interaction,
        _button: ui.Button[EventMessageView],
    ) -> None:
        await self.root.close_room(interaction)


class EventMessageView(ui.LayoutView):
    """Posted RSVP message: local view_state + responses, Discord I/O, controller calls."""

    def __init__(
        self,
        *,
        view_state: ViewState,
        controller: RsvpController,
        settings_store: GuildSettingsStore | None = None,
        message_id: int | None = None,
        channel_id: int | None = None,
        guild_id: int | None = None,
        responses: dict[int, RsvpResponse] | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self.view_state = view_state
        self.responses: dict[int, RsvpResponse] = dict(responses or {})
        self.controller = controller
        self.settings_store = settings_store
        self.message_id = message_id
        self.channel_id = channel_id
        self.guild_id = guild_id
        self.event_card_message_id = view_state.event_card_message_id
        self._rebuild()

    # --- local state / render -------------------------------------------------

    def snapshot(self) -> RsvpRenderSnapshot:
        return RsvpRenderSnapshot(view_state=self.view_state, responses=dict(self.responses))

    def apply_snapshot(self, snapshot: RsvpRenderSnapshot) -> None:
        self.view_state = snapshot.view_state
        self.responses = dict(snapshot.responses)
        self.event_card_message_id = snapshot.view_state.event_card_message_id
        self._rebuild()

    def _rebuild(self) -> None:
        self.clear_items()
        snapshot = self.snapshot()
        self.add_item(RsvpBodyDisplay(snapshot))
        self.add_item(RsvpStatusActions(self))
        self.add_item(RsvpMetaActions(self))
        self.add_item(RoomActions(self))

    def set_event_card_message_id(self, message_id: int | None) -> None:
        """Remember companion event-card message id on local + persisted view state."""
        self.event_card_message_id = message_id
        self.view_state = replace(
            self.view_state,
            event_card_message_id=message_id,
        )
        if (
            self.message_id is not None
            and self.channel_id is not None
            and self.guild_id is not None
        ):
            self.controller.bind_message(
                message_id=self.message_id,
                channel_id=self.channel_id,
                guild_id=self.guild_id,
                view_state=self.view_state,
            )

    def user_ids_for_status(self, status: RsvpStatus) -> list[int]:
        return [
            user_id
            for user_id, response in self.responses.items()
            if response.status == status
        ]

    def user_ids_for_statuses(self, statuses: tuple[RsvpStatus, ...]) -> list[int]:
        user_ids: list[int] = []
        for status in statuses:
            user_ids.extend(self.user_ids_for_status(status))
        return user_ids

    def event_manager_role_id(self, interaction: discord.Interaction) -> int | None:
        if self.settings_store is None:
            return None
        if interaction.guild is not None:
            return self.settings_store.get_event_manager_role_id(interaction.guild.id)
        if self.guild_id is not None:
            return self.settings_store.get_event_manager_role_id(self.guild_id)
        return None

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
        self.controller.bind_message(
            message_id=message_id,
            channel_id=channel_id,
            guild_id=guild_id,
            view_state=self.view_state,
        )

    def _ensure_message_context(self, interaction: discord.Interaction) -> None:
        message = interaction.message
        if message is None:
            return
        if self.message_id is None:
            self.message_id = message.id
        if self.channel_id is None:
            self.channel_id = message.channel.id
        if self.guild_id is None and interaction.guild is not None:
            self.guild_id = interaction.guild.id

    # --- Discord edit helpers -------------------------------------------------

    async def _channel_for_edit(
        self,
        interaction: discord.Interaction,
    ) -> EditableChannel | None:
        if self.channel_id is not None:
            client = interaction.client
            resolved = client.get_channel(self.channel_id)
            if resolved is None:
                with suppress(discord.HTTPException, discord.NotFound):
                    resolved = await client.fetch_channel(self.channel_id)
            if isinstance(resolved, _EDITABLE_CHANNEL_TYPES):
                return resolved
        channel = interaction.channel
        if isinstance(channel, _EDITABLE_CHANNEL_TYPES):
            return channel
        return None

    async def _edit_self(self, interaction: discord.Interaction) -> bool:
        """Edit the posted RSVP message (not an ephemeral followup)."""
        self._ensure_message_context(interaction)

        # Prefer PartialMessage.edit so we do not need a full message fetch.
        if self.message_id is not None:
            channel = await self._channel_for_edit(interaction)
            if channel is not None:
                try:
                    partial = discord.PartialMessage(channel=channel, id=self.message_id)
                    await partial.edit(view=self)
                    return True
                except (discord.HTTPException, discord.NotFound, TypeError):
                    logger.exception(
                        "Failed PartialMessage.edit for RSVP message_id=%s channel_id=%s",
                        self.message_id,
                        self.channel_id,
                    )

        message = interaction.message
        if message is not None and (
            self.message_id is None or message.id == self.message_id
        ):
            try:
                await message.edit(view=self)
            except discord.HTTPException:
                logger.exception("Failed message.edit for RSVP message_id=%s", message.id)
                return False
            return True

        logger.warning(
            "Could not resolve RSVP root message for edit message_id=%s channel_id=%s",
            self.message_id,
            self.channel_id,
        )
        return False

    async def apply_and_edit(
        self,
        interaction: discord.Interaction,
        snapshot: RsvpRenderSnapshot,
    ) -> bool:
        self.apply_snapshot(snapshot)
        return await self._edit_self(interaction)

    async def refresh_message(self, message: discord.Message) -> None:
        snapshot = await self.controller.get_snapshot(
            message_id=self.message_id,
            view_state=self.view_state,
        )
        self.apply_snapshot(snapshot)
        with suppress(discord.HTTPException):
            await message.edit(view=self)

    async def _reply_service_error(
        self,
        interaction: discord.Interaction,
        error: ServiceError,
        *,
        deferred: bool = False,
    ) -> None:
        match error.code:
            case "invalid_room_url":
                content = ROOM_LINK_MODAL_INVALID_MESSAGE
            case "event_not_found" | "event_not_reusable":
                content = SELECT_EVENT_EMPTY_MESSAGE
            case _:
                logger.warning("Unhandled service error code=%s", error.code)
                content = _GENERIC_SERVICE_ERROR_MESSAGE
        if deferred or interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=True)
        else:
            await interaction.response.send_message(content, ephemeral=True)

    # --- interaction handlers -------------------------------------------------

    async def set_status(
        self,
        interaction: discord.Interaction,
        status: RsvpStatus,
    ) -> None:
        await interaction.response.defer(thinking=False)
        result = await self.controller.set_status(
            message_id=self.message_id,
            view_state=self.view_state,
            user_id=interaction.user.id,
            status=status,
        )
        match result:
            case Ok(snapshot):
                await self.apply_and_edit(interaction, snapshot)
            case Err(error):
                await self._reply_service_error(interaction, error, deferred=True)

    async def toggle_storyteller(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=False)
        result = await self.controller.toggle_storyteller(
            message_id=self.message_id,
            view_state=self.view_state,
            user_id=interaction.user.id,
        )
        match result:
            case Ok(snapshot):
                await self.apply_and_edit(interaction, snapshot)
            case Err(error):
                await self._reply_service_error(interaction, error, deferred=True)

    async def open_arrive_later_modal(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            ArriveLaterModal(on_submit=self.submit_arrival_time),
        )

    async def submit_arrival_time(
        self,
        interaction: discord.Interaction,
        raw_time: str,
    ) -> None:
        arrival_time = parse_arrival_time(raw_time)
        if arrival_time is None:
            await interaction.response.send_message(
                ARRIVE_LATER_INVALID_TIME_MESSAGE,
                ephemeral=True,
            )
            return
        await interaction.response.defer(thinking=False)
        result = await self.controller.set_arrival_time(
            message_id=self.message_id,
            view_state=self.view_state,
            user_id=interaction.user.id,
            arrival_time=arrival_time,
        )
        match result:
            case Ok(snapshot):
                await self.apply_and_edit(interaction, snapshot)
            case Err(error):
                await self._reply_service_error(interaction, error, deferred=True)

    async def open_room_modal(self, interaction: discord.Interaction) -> None:
        role_id = self.event_manager_role_id(interaction)
        if not can_manage_room_action(interaction, event_manager_role_id=role_id):
            await interaction.response.send_message(
                room_permission_denied_message(interaction, event_manager_role_id=role_id),
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            RoomLinkModal(on_submit=self.submit_room_link),
        )

    async def submit_room_link(
        self,
        interaction: discord.Interaction,
        raw_url: str,
    ) -> None:
        role_id = self.event_manager_role_id(interaction)
        if not can_manage_room_action(interaction, event_manager_role_id=role_id):
            await interaction.response.send_message(
                room_permission_denied_message(interaction, event_manager_role_id=role_id),
                ephemeral=True,
            )
            return
        try:
            normalized = normalize_room_url(raw_url.strip())
        except ValueError:
            normalized = None
        if normalized is None:
            await interaction.response.send_message(
                ROOM_LINK_MODAL_INVALID_MESSAGE,
                ephemeral=True,
            )
            return
        await interaction.response.defer(thinking=False)
        result = await self.controller.open_room(
            message_id=self.message_id,
            view_state=self.view_state,
            room_url=normalized,
        )
        match result:
            case Ok((_new_state, snapshot)):
                await self.apply_and_edit(interaction, snapshot)
                if interaction.message is not None:
                    await announce_room_opening(
                        interaction=interaction,
                        user_ids=self.user_ids_for_statuses(("Available", "Maybe")),
                        message_link=interaction.message.jump_url,
                    )
            case Err(error):
                await self._reply_service_error(interaction, error, deferred=True)

    async def close_room(self, interaction: discord.Interaction) -> None:
        role_id = self.event_manager_role_id(interaction)
        if not can_manage_room_action(interaction, event_manager_role_id=role_id):
            await interaction.response.send_message(
                room_permission_denied_message(interaction, event_manager_role_id=role_id),
                ephemeral=True,
            )
            return
        await interaction.response.defer(thinking=False)
        result = await self.controller.close_room(
            message_id=self.message_id,
            view_state=self.view_state,
        )
        match result:
            case Ok((_new_state, snapshot)):
                await self.apply_and_edit(interaction, snapshot)
            case Err(error):
                await self._reply_service_error(interaction, error, deferred=True)

    async def open_event_select(self, interaction: discord.Interaction) -> None:
        """Show only a modal with a dropdown of pickable cached events."""
        role_id = self.event_manager_role_id(interaction)
        if not can_manage_room_action(interaction, event_manager_role_id=role_id):
            await interaction.response.send_message(
                room_permission_denied_message(interaction, event_manager_role_id=role_id),
                ephemeral=True,
            )
            return

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                _GUILD_REQUIRED_MESSAGE,
                ephemeral=True,
            )
            return

        self._ensure_message_context(interaction)
        cached = list(AUTOCOMPLETE_EVENT_CACHE.get(guild.id, []))
        options = build_event_select_options(choices=cached, include_create_new=False)
        if not options:
            await interaction.response.send_message(
                SELECT_EVENT_EMPTY_MESSAGE,
                ephemeral=True,
            )
            return

        # First response must be the modal (no defer / extra UI on this path).
        await interaction.response.send_modal(
            SelectEventModal(on_submit=self.apply_selected_event, options=options),
        )

    async def apply_selected_event(
        self,
        interaction: discord.Interaction,
        selected_value: str,
    ) -> None:
        """Apply the dropdown choice: update linked event state and refresh the RSVP view."""
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                _GUILD_REQUIRED_MESSAGE,
                ephemeral=True,
            )
            return

        role_id = self.event_manager_role_id(interaction)
        if not can_manage_room_action(interaction, event_manager_role_id=role_id):
            await interaction.response.send_message(
                room_permission_denied_message(interaction, event_manager_role_id=role_id),
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=False)

        resolution = await resolve_existing_event_for_command(
            guild=guild,
            selected_event_value=selected_value,
        )
        if resolution.error_message is not None or resolution.event is None:
            await interaction.followup.send(
                resolution.error_message or SELECT_EVENT_EMPTY_MESSAGE,
                ephemeral=True,
            )
            return

        event = resolution.event
        event_url = build_event_url(guild_id=guild.id, event_id=event.id)
        result = await self.controller.update_linked_event(
            message_id=self.message_id,
            view_state=self.view_state,
            event_name=event.name,
            event_url=event_url,
            start_unix=event_start_unix(event=event),
            event_card_message_id=self.event_card_message_id,
        )
        match result:
            case Ok((_new_state, snapshot)):
                edited = await self.apply_and_edit(interaction, snapshot)
                if not edited:
                    await interaction.followup.send(
                        _LINK_EDIT_FAILED_MESSAGE,
                        ephemeral=True,
                    )
                    return
                # Keep/create companion bare-URL message (native Discord event card).
                await self.sync_event_card_message(interaction, event_url=event_url)
            case Err(error):
                await self._reply_service_error(interaction, error, deferred=True)

    async def sync_event_card_message(
        self,
        interaction: discord.Interaction,
        *,
        event_url: str,
    ) -> None:
        """Edit the companion slot to the scheduled-event URL (or post if missing).

        New RSVPs always create a companion above the RSVP (placeholder or URL).
        Late-link should therefore almost always hit the edit path. Posting a new
        companion is only a fallback for older messages or a deleted card, and may
        land below the RSVP.
        """
        if not is_discord_scheduled_event_url(event_url):
            return
        channel = await self._channel_for_edit(interaction)
        if channel is None:
            logger.warning(
                "Could not sync event card; no channel message_id=%s channel_id=%s",
                self.message_id,
                self.channel_id,
            )
            return

        card_id = self.event_card_message_id or self.view_state.event_card_message_id
        if card_id is not None:
            try:
                partial = discord.PartialMessage(channel=channel, id=card_id)
                await partial.edit(content=event_url)
                return
            except (discord.HTTPException, discord.NotFound, TypeError):
                logger.exception(
                    "Failed editing event card message_id=%s; will post a new one",
                    card_id,
                )
                self.set_event_card_message_id(None)

        try:
            posted = await channel.send(content=event_url)
        except (discord.HTTPException, discord.Forbidden):
            logger.exception("Failed posting event card url=%s", event_url)
            return
        self.set_event_card_message_id(posted.id)

