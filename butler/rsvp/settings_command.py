from __future__ import annotations

from collections.abc import Callable

import discord
from discord.ext import commands

from butler.permissions import format_permissions, get_missing_post_permissions
from butler.settings_store import GuildSettingsStore


async def handle_seteventchannel_command(
    *,
    interaction: discord.Interaction,
    event_channel: discord.TextChannel,
    bot: commands.Bot,
    settings_store: GuildSettingsStore,
    get_bot_member_fn: Callable[[discord.Guild, discord.ClientUser | None], discord.Member | None],
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            "This command must be used in a server.",
            ephemeral=True,
        )
        return

    if event_channel.guild.id != interaction.guild.id:
        await interaction.response.send_message(
            "Select a text channel from this server.",
            ephemeral=True,
        )
        return

    bot_member = get_bot_member_fn(interaction.guild, bot.user)
    if bot_member is None:
        await interaction.response.send_message(
            "I couldn't verify my server permissions. Re-invite the bot and try again.",
            ephemeral=True,
        )
        return

    missing_permissions = get_missing_post_permissions(
        bot_member=bot_member,
        event_channel=event_channel,
    )
    if missing_permissions:
        await interaction.response.send_message(
            "I can't use that channel yet. Missing permissions in "
            f"{event_channel.mention}: {format_permissions(missing_permissions)}",
            ephemeral=True,
        )
        return

    try:
        settings_store.set_default_event_channel_id(interaction.guild.id, event_channel.id)
    except OSError:
        await interaction.response.send_message(
            "Failed to save the channel setting. Try again.",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        f"Default event channel set to {event_channel.mention}. "
        "You can change it anytime with `/seteventchannel`.",
        ephemeral=True,
    )


async def handle_seteventrole_command(
    *,
    interaction: discord.Interaction,
    role: discord.Role | None,
    settings_store: GuildSettingsStore,
) -> None:
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "This command must be used in a server.",
            ephemeral=True,
        )
        return

    if role is not None and role.guild.id != guild.id:
        await interaction.response.send_message(
            "Select a role from this server.",
            ephemeral=True,
        )
        return

    try:
        if role is None:
            settings_store.clear_event_manager_role_id(guild.id)
        else:
            settings_store.set_event_manager_role_id(guild.id, role.id)
    except OSError:
        await interaction.response.send_message(
            "Failed to save the role setting. Try again.",
            ephemeral=True,
        )
        return

    if role is None:
        await interaction.response.send_message(
            "Cleared the event manager role. Only users with "
            "`Hantera server` can now create events and open/close rooms.",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        (
            f"Members with {role.mention} can now create events and open/close rooms "
            "(in addition to `Hantera server`)."
        ),
        ephemeral=True,
    )
