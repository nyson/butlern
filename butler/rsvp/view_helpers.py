from __future__ import annotations

from contextlib import suppress
from typing import Protocol

import discord

from butler.design import (
    ROOM_LINK_PERMISSION_DENIED_MESSAGE,
    ROOM_LINK_PERMISSION_DENIED_ROLE_TEMPLATE,
    ROOM_OPENED_NO_MENTIONS_TEMPLATE,
    ROOM_OPENED_WITH_MENTIONS_TEMPLATE,
)
from butler.permissions import member_can_manage_events, permission_denied_message
from butler.rsvp.types import RsvpStatus


class AvailabilityViewProtocol(Protocol):
    async def get_user_ids_for_status(self, status: RsvpStatus) -> list[int]:
        ...


def build_user_mentions(user_ids: list[int]) -> str:
    unique_user_ids = sorted(set(user_ids))
    return " ".join(f"<@{user_id}>" for user_id in unique_user_ids)


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


async def announce_room_opening(
    *,
    interaction: discord.Interaction,
    availability_view: AvailabilityViewProtocol,
    message_link: str,
) -> None:
    statuses_to_ping: tuple[RsvpStatus, ...] = ("Available", "Maybe")
    user_ids_to_ping: list[int] = []
    for status in statuses_to_ping:
        user_ids_to_ping.extend(await availability_view.get_user_ids_for_status(status))
    mentions = build_user_mentions(user_ids_to_ping)

    channel = interaction.channel
    if isinstance(channel, (discord.TextChannel, discord.Thread)):
        if mentions:
            content = ROOM_OPENED_WITH_MENTIONS_TEMPLATE.format(
                mentions=mentions,
                message_link=message_link,
            )
        else:
            content = ROOM_OPENED_NO_MENTIONS_TEMPLATE.format(
                message_link=message_link,
            )
        with suppress(discord.HTTPException):
            await channel.send(
                content,
                allowed_mentions=discord.AllowedMentions(
                    users=True,
                    roles=False,
                    everyone=False,
                ),
            )
