from __future__ import annotations

from collections.abc import Sequence
from contextlib import suppress

import discord
from discord.ext import commands

from butler.design import (
    ROOM_OPENED_NO_MENTIONS_TEMPLATE,
    ROOM_OPENED_WITH_MENTIONS_TEMPLATE,
)


def get_bot_member(
    guild: discord.Guild,
    bot_user: discord.ClientUser | None,
) -> discord.Member | None:
    if bot_user is None:
        return None
    member = guild.get_member(bot_user.id)
    if member is not None:
        return member
    return guild.me


def resolve_text_channel(guild: discord.Guild, channel_id: int) -> discord.TextChannel | None:
    channel = guild.get_channel(channel_id)
    if isinstance(channel, discord.TextChannel):
        return channel
    return None


async def fetch_message_from_channel(
    *,
    bot: commands.Bot,
    channel_id: int,
    message_id: int,
) -> discord.Message | None:
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        return None

    try:
        return await channel.fetch_message(message_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None


def build_user_mentions(user_ids: Sequence[int]) -> str:
    unique_user_ids = sorted(set(user_ids))
    return " ".join(f"<@{user_id}>" for user_id in unique_user_ids)


async def announce_room_opening(
    *,
    interaction: discord.Interaction,
    user_ids: Sequence[int],
    message_link: str,
) -> None:
    mentions = build_user_mentions(user_ids)
    channel = interaction.channel
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        return
    if mentions:
        content = ROOM_OPENED_WITH_MENTIONS_TEMPLATE.format(
            mentions=mentions,
            message_link=message_link,
        )
    else:
        content = ROOM_OPENED_NO_MENTIONS_TEMPLATE.format(message_link=message_link)
    with suppress(discord.HTTPException):
        await channel.send(
            content,
            allowed_mentions=discord.AllowedMentions(
                users=True,
                roles=False,
                everyone=False,
            ),
        )
