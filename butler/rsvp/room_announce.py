from __future__ import annotations

from collections.abc import Sequence
from contextlib import suppress
from typing import Literal

import discord

from butler.design import PLAYER_ROLE_NAME, ROOM_OPENED_ANNOUNCE_TEMPLATE

# Who the room announcement mentions: nobody, tonight's RSVPs, or the @Spelare role.
PingMode = Literal["none", "rsvp", "players"]
PING_MODES: tuple[PingMode, ...] = ("none", "rsvp", "players")
DEFAULT_PING_MODE: PingMode = "rsvp"


def find_player_role(guild: discord.Guild) -> discord.Role | None:
    """Return the guild's @Spelare role (case-insensitive name match), if any."""
    wanted = PLAYER_ROLE_NAME.casefold()
    return next((role for role in guild.roles if role.name.casefold() == wanted), None)


def build_user_mentions(user_ids: Sequence[int]) -> str:
    unique_user_ids = sorted(set(user_ids))
    return " ".join(f"<@{user_id}>" for user_id in unique_user_ids)


def build_room_opened_content(
    *,
    user_ids: Sequence[int],
    room_url: str,
    optional_message: str | None = None,
    role_id: int | None = None,
) -> str:
    """Build room-open announce text in the product layout.

    Hej [users]!

    Rummet är öppet: [room link]

    [optional message]

    ``role_id`` (when set) is mentioned instead of the individual users.
    """
    mentions = f"<@&{role_id}>" if role_id is not None else build_user_mentions(user_ids)
    greeting = f"Hej {mentions}!" if mentions else "Hej!"
    extra = (optional_message or "").strip()
    body = ROOM_OPENED_ANNOUNCE_TEMPLATE.format(
        greeting=greeting,
        room_url=room_url,
    )
    if extra:
        return f"{body}\n\n{extra}"
    return body


async def announce_room_opening(
    *,
    interaction: discord.Interaction,
    room_url: str,
    user_ids: Sequence[int] = (),
    role: discord.Role | None = None,
    optional_message: str | None = None,
    announcement_channel: discord.abc.Messageable | None = None,
) -> None:
    """Post a channel notice when an RSVP room is opened.

    Prefer ``announcement_channel`` when configured; otherwise the interaction
    channel (usually the RSVP post channel). ``room_url`` is posted bare (no
    markdown). Mentions ``role`` when given, otherwise ``user_ids``; with neither,
    the notice is posted without pings.
    """
    channel = announcement_channel if announcement_channel is not None else interaction.channel
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        return
    content = build_room_opened_content(
        user_ids=user_ids,
        room_url=room_url,
        optional_message=optional_message,
        role_id=role.id if role is not None else None,
    )
    with suppress(discord.HTTPException):
        await channel.send(
            content,
            allowed_mentions=discord.AllowedMentions(
                users=True,
                roles=[role] if role is not None else False,
                everyone=False,
            ),
            # Keep room URLs clickable without link-preview embeds.
            suppress_embeds=True,
        )
