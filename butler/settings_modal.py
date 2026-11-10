from __future__ import annotations

from collections.abc import Awaitable, Callable

import discord
from discord import SelectDefaultValue, SelectDefaultValueType, ui

from butler.design import (
    SETTINGS_ANNOUNCEMENT_CHANNEL_LABEL,
    SETTINGS_ANNOUNCEMENT_CHANNEL_PLACEHOLDER,
    SETTINGS_EVENT_CHANNEL_LABEL,
    SETTINGS_EVENT_CHANNEL_PLACEHOLDER,
    SETTINGS_EVENT_ROLE_LABEL,
    SETTINGS_EVENT_ROLE_PLACEHOLDER,
    SETTINGS_MODAL_TITLE,
)
from butler.settings_store import GuildSettings

SubmitGuildSettings = Callable[
    [discord.Interaction, GuildSettings],
    Awaitable[None],
]


def _channel_defaults(channel_id: int | None) -> list[SelectDefaultValue]:
    if channel_id is None:
        return []
    return [SelectDefaultValue(id=channel_id, type=SelectDefaultValueType.channel)]


def _role_defaults(role_id: int | None) -> list[SelectDefaultValue]:
    if role_id is None:
        return []
    return [SelectDefaultValue(id=role_id, type=SelectDefaultValueType.role)]


def _selected_channel_id(select: ui.ChannelSelect[GuildSettingsModal]) -> int | None:
    values = select.values
    if not values:
        return None
    return values[0].id


def _selected_role_id(select: ui.RoleSelect[GuildSettingsModal]) -> int | None:
    values = select.values
    if not values:
        return None
    return values[0].id


class GuildSettingsModal(ui.Modal, title=SETTINGS_MODAL_TITLE):
    """Collect event channel, announcement channel, and event-manager role."""

    def __init__(
        self,
        *,
        on_submit: SubmitGuildSettings,
        current: GuildSettings,
    ) -> None:
        super().__init__()
        self._on_submit = on_submit

        self._event_channel: ui.ChannelSelect[GuildSettingsModal] = ui.ChannelSelect(
            custom_id="butler:settings:event-channel",
            channel_types=[discord.ChannelType.text],
            placeholder=SETTINGS_EVENT_CHANNEL_PLACEHOLDER,
            min_values=1,
            max_values=1,
            required=True,
            default_values=_channel_defaults(current.event_channel_id),
        )
        self._announcement_channel: ui.ChannelSelect[GuildSettingsModal] = ui.ChannelSelect(
            custom_id="butler:settings:announcement-channel",
            channel_types=[discord.ChannelType.text],
            placeholder=SETTINGS_ANNOUNCEMENT_CHANNEL_PLACEHOLDER,
            min_values=0,
            max_values=1,
            required=False,
            default_values=_channel_defaults(current.event_announcement_channel_id),
        )
        self._event_role: ui.RoleSelect[GuildSettingsModal] = ui.RoleSelect(
            custom_id="butler:settings:event-role",
            placeholder=SETTINGS_EVENT_ROLE_PLACEHOLDER,
            min_values=0,
            max_values=1,
            required=False,
            default_values=_role_defaults(current.event_manager_role_id),
        )

        self.add_item(
            ui.Label(
                text=SETTINGS_EVENT_CHANNEL_LABEL,
                component=self._event_channel,
            )
        )
        self.add_item(
            ui.Label(
                text=SETTINGS_ANNOUNCEMENT_CHANNEL_LABEL,
                component=self._announcement_channel,
            )
        )
        self.add_item(
            ui.Label(
                text=SETTINGS_EVENT_ROLE_LABEL,
                component=self._event_role,
            )
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        event_channel_id = _selected_channel_id(self._event_channel)
        if event_channel_id is None:
            await interaction.response.send_message(
                "Du måste välja en eventkanal.",
                ephemeral=True,
            )
            return
        settings = GuildSettings(
            event_channel_id=event_channel_id,
            event_announcement_channel_id=_selected_channel_id(self._announcement_channel),
            event_manager_role_id=_selected_role_id(self._event_role),
        )
        await self._on_submit(interaction, settings)
