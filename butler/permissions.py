from __future__ import annotations

from collections.abc import Sequence

import discord


def guild_sync_access_message(guild_id: int) -> str:
    return (
        f"Cannot sync commands to guild {guild_id} (Discord 50001 Missing Access). "
        "Check that [discord].guild_id is correct, the bot is invited to that server with "
        "OAuth scopes `bot` and `applications.commands`, and the bot role can at least "
        "`View Channels` and `Use Application Commands`."
    )


def format_permissions(permission_names: Sequence[str]) -> str:
    return ", ".join(f"`{name}`" for name in permission_names)


def get_missing_event_permissions(*, bot_member: discord.Member) -> list[str]:
    guild_permissions = bot_member.guild_permissions
    missing: list[str] = []
    if not guild_permissions.create_events:
        missing.append("Create Events")
    if not guild_permissions.use_application_commands:
        missing.append("Use Application Commands")
    return missing


def get_missing_post_permissions(
    *,
    bot_member: discord.Member,
    event_channel: discord.TextChannel,
) -> list[str]:
    channel_permissions = event_channel.permissions_for(bot_member)
    missing: list[str] = []
    if not channel_permissions.view_channel:
        missing.append("View Channel")
    if not channel_permissions.send_messages:
        missing.append("Send Messages")
    if not channel_permissions.embed_links:
        missing.append("Embed Links")
    return missing


def find_onboarding_channel(
    guild: discord.Guild,
    bot_member: discord.Member,
) -> discord.TextChannel | None:
    if guild.system_channel is not None:
        perms = guild.system_channel.permissions_for(bot_member)
        if perms.view_channel and perms.send_messages:
            return guild.system_channel

    for channel in guild.text_channels:
        perms = channel.permissions_for(bot_member)
        if perms.view_channel and perms.send_messages:
            return channel
    return None
