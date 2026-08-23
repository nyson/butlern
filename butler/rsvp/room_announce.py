from __future__ import annotations

from collections.abc import Sequence
from contextlib import suppress

import discord

from butler.design import (
    ROOM_OPENED_NO_MENTIONS_TEMPLATE,
    ROOM_OPENED_WITH_MENTIONS_TEMPLATE,
)


def build_user_mentions(user_ids: Sequence[int]) -> str:
    unique_user_ids = sorted(set(user_ids))
    return " ".join(f"<@{user_id}>" for user_id in unique_user_ids)


async def announce_room_opening(
    *,
    interaction: discord.Interaction,
    user_ids: Sequence[int],
    message_link: str,
) -> None:
    """Post a channel ping when an RSVP room is opened."""
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
