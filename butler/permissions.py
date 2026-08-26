from __future__ import annotations

import discord

from butler.design import (
    ROOM_LINK_PERMISSION_DENIED_MESSAGE,
    ROOM_LINK_PERMISSION_DENIED_ROLE_TEMPLATE,
    SELECT_EVENT_PERMISSION_DENIED_MESSAGE,
    SELECT_EVENT_PERMISSION_DENIED_ROLE_TEMPLATE,
)
from butler.domains.permissions.domain import can_manage_events as can_manage_events
from butler.domains.permissions.domain import format_permissions as format_permissions
from butler.domains.permissions.domain import (
    permission_denied_message as permission_denied_message,
)


def guild_sync_access_message(guild_id: int) -> str:
    return (
        f"Cannot sync commands to guild {guild_id} (Discord 50001 Missing Access). "
        "Check that [discord].guild_id is correct, the bot is invited to that server with "
        "OAuth scopes `bot` and `applications.commands`, and the bot role can at least "
        "`View Channels` and `Use Application Commands`."
    )


def member_can_manage_events(
    member: discord.Member,
    *,
    event_manager_role_id: int | None,
) -> bool:
    """Shell adapter: project a `discord.Member` onto the pure `can_manage_events`."""
    return can_manage_events(
        has_manage_guild=member.guild_permissions.manage_guild,
        member_role_ids={role.id for role in member.roles},
        event_manager_role_id=event_manager_role_id,
    )


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
        without_role=ROOM_LINK_PERMISSION_DENIED_MESSAGE,
        with_role_template=ROOM_LINK_PERMISSION_DENIED_ROLE_TEMPLATE,
    )


def select_event_permission_denied_message(
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
        without_role=SELECT_EVENT_PERMISSION_DENIED_MESSAGE,
        with_role_template=SELECT_EVENT_PERMISSION_DENIED_ROLE_TEMPLATE,
    )


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
