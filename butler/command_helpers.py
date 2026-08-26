from __future__ import annotations

from collections.abc import Callable

import discord
from discord.ext import commands

from butler.design import (
    EVENT_MANAGEMENT_PERMISSION_DENIED_MESSAGE,
    EVENT_MANAGEMENT_PERMISSION_DENIED_ROLE_TEMPLATE,
)
from butler.discord_events import BOT_PERMISSION_VERIFY_FAILURE_MESSAGE
from butler.permissions import (
    format_permissions,
    get_missing_event_permissions,
    get_missing_post_permissions,
    member_can_manage_events,
    permission_denied_message,
)
from butler.settings_store import GuildSettingsStore


def configured_event_manager_role_id(
    *,
    settings_store: GuildSettingsStore,
    guild_id: int,
) -> int | None:
    return settings_store.get_event_manager_role_id(guild_id)


def event_management_permission_denied_message(
    *,
    guild: discord.Guild,
    event_manager_role_id: int | None,
) -> str:
    role = guild.get_role(event_manager_role_id) if event_manager_role_id is not None else None
    return permission_denied_message(
        role_mention=role.mention if role is not None else None,
        without_role=EVENT_MANAGEMENT_PERMISSION_DENIED_MESSAGE,
        with_role_template=EVENT_MANAGEMENT_PERMISSION_DENIED_ROLE_TEMPLATE,
    )


def resolve_configured_event_channel(
    *,
    settings_store: GuildSettingsStore,
    guild: discord.Guild,
    resolve_text_channel_fn: Callable[[discord.Guild, int], discord.TextChannel | None],
) -> discord.TextChannel | None:
    channel_id = settings_store.get_default_event_channel_id(guild.id)
    if channel_id is None:
        return None
    return resolve_text_channel_fn(guild, channel_id)


def missing_permission_details(
    *,
    bot_member: discord.Member,
    event_channel: discord.TextChannel,
) -> list[str]:
    missing_event_permissions = get_missing_event_permissions(bot_member=bot_member)
    missing_post_permissions = get_missing_post_permissions(
        bot_member=bot_member,
        event_channel=event_channel,
    )
    details: list[str] = []
    if missing_event_permissions:
        details.append(f"Server-level missing: {format_permissions(missing_event_permissions)}")
    if missing_post_permissions:
        details.append(
            f"Missing in {event_channel.mention}: "
            f"{format_permissions(missing_post_permissions)}"
        )
    return details


async def defer_thinking_response(interaction: discord.Interaction) -> bool:
    try:
        await interaction.response.defer(ephemeral=True, thinking=True)
        return True
    except discord.InteractionResponded:
        return True
    except discord.NotFound as exc:
        if exc.code != 10062:
            raise
        return False
    except discord.HTTPException:
        return False


async def ensure_event_creation_permissions(
    *,
    interaction: discord.Interaction,
    guild: discord.Guild,
    event_channel: discord.TextChannel,
    bot: commands.Bot,
    get_bot_member_fn: Callable[[discord.Guild, discord.ClientUser | None], discord.Member | None],
) -> bool:
    bot_member = get_bot_member_fn(guild, bot.user)
    if bot_member is None:
        await interaction.followup.send(
            BOT_PERMISSION_VERIFY_FAILURE_MESSAGE,
            ephemeral=True,
        )
        return False
    permission_errors = missing_permission_details(
        bot_member=bot_member,
        event_channel=event_channel,
    )
    if permission_errors:
        await interaction.followup.send(
            "I don't have the required permissions to create and post this event.\n"
            + "\n".join(permission_errors),
            ephemeral=True,
        )
        return False
    return True


async def ensure_event_post_permissions(
    *,
    interaction: discord.Interaction,
    guild: discord.Guild,
    event_channel: discord.TextChannel,
    bot: commands.Bot,
    get_bot_member_fn: Callable[[discord.Guild, discord.ClientUser | None], discord.Member | None],
) -> bool:
    bot_member = get_bot_member_fn(guild, bot.user)
    if bot_member is None:
        await interaction.followup.send(
            BOT_PERMISSION_VERIFY_FAILURE_MESSAGE,
            ephemeral=True,
        )
        return False
    missing_permissions = get_missing_post_permissions(
        bot_member=bot_member,
        event_channel=event_channel,
    )
    if missing_permissions:
        await interaction.followup.send(
            (
                "I can't post the RSVP in "
                f"{event_channel.mention}: {format_permissions(missing_permissions)}"
            ),
            ephemeral=True,
        )
        return False
    return True


async def resolve_event_command_context(
    interaction: discord.Interaction,
) -> tuple[discord.Guild, discord.Member] | None:
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "This command must be used in a server.",
            ephemeral=True,
        )
        return None
    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            "I couldn't verify your server member permissions.",
            ephemeral=True,
        )
        return None
    return guild, interaction.user


async def ensure_member_can_manage_events_for_command(
    *,
    interaction: discord.Interaction,
    guild: discord.Guild,
    member: discord.Member,
    event_manager_role_id: int | None,
) -> bool:
    if member_can_manage_events(
        member,
        event_manager_role_id=event_manager_role_id,
    ):
        return True
    await interaction.response.send_message(
        event_management_permission_denied_message(
            guild=guild,
            event_manager_role_id=event_manager_role_id,
        ),
        ephemeral=True,
    )
    return False
