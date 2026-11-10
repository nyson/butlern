from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import discord
from discord.ext import commands

from butler.design import (
    MANAGE_SERVER_PERMISSION_LABEL,
    SETTINGS_SAVE_FAILED_MESSAGE,
    SETTINGS_SAVED_TEMPLATE,
)
from butler.permissions import format_permissions, get_missing_post_permissions
from butler.settings_modal import GuildSettingsModal
from butler.settings_store import GuildSettings, GuildSettingsStore


@dataclass(frozen=True)
class _ResolvedSettingsChannels:
    event_channel: discord.TextChannel
    announcement_channel: discord.TextChannel | None
    settings: GuildSettings


def _role_from_guild(
    *,
    guild: discord.Guild,
    role_id: int | None,
) -> discord.Role | None:
    if role_id is None:
        return None
    return guild.get_role(role_id)


async def _resolve_guild_text_channel(
    *,
    guild: discord.Guild,
    channel_id: int | None,
    bot: commands.Bot,
) -> discord.TextChannel | None:
    if channel_id is None:
        return None
    cached = guild.get_channel(channel_id)
    if isinstance(cached, discord.TextChannel) and cached.guild.id == guild.id:
        return cached
    try:
        fetched = await bot.fetch_channel(channel_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None
    if isinstance(fetched, discord.TextChannel) and fetched.guild.id == guild.id:
        return fetched
    return None


def _format_channel_mention(channel: discord.TextChannel | None, *, empty: str) -> str:
    if channel is None:
        return empty
    return channel.mention


def _format_role_mention(role: discord.Role | None, *, empty: str) -> str:
    if role is None:
        return empty
    return role.mention


def _missing_channel_permissions_message(
    *,
    bot_member: discord.Member,
    channel: discord.TextChannel,
    label: str,
) -> str | None:
    missing = get_missing_post_permissions(bot_member=bot_member, event_channel=channel)
    if not missing:
        return None
    return (
        f"I can't use the {label} {channel.mention} yet. "
        f"Missing permissions: {format_permissions(missing)}"
    )


async def _validate_settings_payload(
    *,
    guild: discord.Guild,
    settings: GuildSettings,
    bot: commands.Bot,
    bot_member: discord.Member,
) -> _ResolvedSettingsChannels | str:
    if settings.event_channel_id is None:
        return "Du måste välja en eventkanal."

    event_channel = await _resolve_guild_text_channel(
        guild=guild,
        channel_id=settings.event_channel_id,
        bot=bot,
    )
    if event_channel is None:
        return "Select a text channel from this server for event posts."

    announcement_channel: discord.TextChannel | None = None
    if settings.event_announcement_channel_id is not None:
        announcement_channel = await _resolve_guild_text_channel(
            guild=guild,
            channel_id=settings.event_announcement_channel_id,
            bot=bot,
        )
        if announcement_channel is None:
            return "Select a text channel from this server for announcements."

    if settings.event_manager_role_id is not None:
        role = _role_from_guild(guild=guild, role_id=settings.event_manager_role_id)
        if role is None:
            return "Select a role from this server."

    event_perm_error = _missing_channel_permissions_message(
        bot_member=bot_member,
        channel=event_channel,
        label="event channel",
    )
    if event_perm_error is not None:
        return event_perm_error

    if announcement_channel is not None:
        announce_perm_error = _missing_channel_permissions_message(
            bot_member=bot_member,
            channel=announcement_channel,
            label="announcement channel",
        )
        if announce_perm_error is not None:
            return announce_perm_error

    return _ResolvedSettingsChannels(
        event_channel=event_channel,
        announcement_channel=announcement_channel,
        settings=GuildSettings(
            event_channel_id=event_channel.id,
            event_announcement_channel_id=(
                announcement_channel.id if announcement_channel is not None else None
            ),
            event_manager_role_id=settings.event_manager_role_id,
        ),
    )


async def handle_settings_command(
    *,
    interaction: discord.Interaction,
    bot: commands.Bot,
    settings_store: GuildSettingsStore,
    get_bot_member_fn: Callable[[discord.Guild, discord.ClientUser | None], discord.Member | None],
) -> None:
    """Open the guild settings modal for Manage Server users."""
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "This command must be used in a server.",
            ephemeral=True,
        )
        return

    current = settings_store.get_settings(guild.id)

    async def _on_submit(
        submit_interaction: discord.Interaction,
        settings: GuildSettings,
    ) -> None:
        await handle_settings_modal_submit(
            interaction=submit_interaction,
            settings=settings,
            bot=bot,
            settings_store=settings_store,
            get_bot_member_fn=get_bot_member_fn,
        )

    await interaction.response.send_modal(
        GuildSettingsModal(on_submit=_on_submit, current=current),
    )


async def handle_settings_modal_submit(
    *,
    interaction: discord.Interaction,
    settings: GuildSettings,
    bot: commands.Bot,
    settings_store: GuildSettingsStore,
    get_bot_member_fn: Callable[[discord.Guild, discord.ClientUser | None], discord.Member | None],
) -> None:
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "This command must be used in a server.",
            ephemeral=True,
        )
        return

    bot_member = get_bot_member_fn(guild, bot.user)
    if bot_member is None:
        await interaction.response.send_message(
            "I couldn't verify my server permissions. Re-invite the bot and try again.",
            ephemeral=True,
        )
        return

    validated = await _validate_settings_payload(
        guild=guild,
        settings=settings,
        bot=bot,
        bot_member=bot_member,
    )
    if isinstance(validated, str):
        await interaction.response.send_message(validated, ephemeral=True)
        return

    try:
        settings_store.save_settings(guild.id, validated.settings)
    except OSError:
        await interaction.response.send_message(
            SETTINGS_SAVE_FAILED_MESSAGE,
            ephemeral=True,
        )
        return

    role = _role_from_guild(guild=guild, role_id=validated.settings.event_manager_role_id)
    await interaction.response.send_message(
        SETTINGS_SAVED_TEMPLATE.format(
            event_channel=validated.event_channel.mention,
            announcement_channel=_format_channel_mention(
                validated.announcement_channel,
                empty="same as the RSVP message channel",
            ),
            event_role=_format_role_mention(
                role,
                empty=f"only {MANAGE_SERVER_PERMISSION_LABEL}",
            ),
        ),
        ephemeral=True,
    )
