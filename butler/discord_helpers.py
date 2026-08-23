from __future__ import annotations

import discord
from discord.ext import commands


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
