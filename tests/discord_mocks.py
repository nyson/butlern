"""Spec'd mock factories for testing the imperative shell (`butler.app`).

The command handlers use `isinstance(..., discord.Member)` / `discord.TextChannel` guards,
so the objects passed in must satisfy those checks. `unittest.mock.MagicMock(spec=...)`
does exactly that while still recording calls; `AsyncMock` covers the awaitable surfaces
(`interaction.response`, `followup`, `channel.send`, `message.edit`, ...).

Factories return the values cast to the real Discord type (so handler calls type-check),
while the `Ix` harness also exposes the raw `AsyncMock` recorders for assertions.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import discord

_PERMISSION_FLAGS = (
    "manage_guild",
    "create_events",
    "use_application_commands",
    "view_channel",
    "send_messages",
    "embed_links",
)


def make_permissions(**overrides: bool) -> discord.Permissions:
    """A permissions object where every flag we check defaults to True unless overridden."""
    perms = MagicMock(spec=discord.Permissions)
    for flag in _PERMISSION_FLAGS:
        setattr(perms, flag, overrides.get(flag, True))
    return cast(discord.Permissions, perms)


def make_role(*, role_id: int) -> discord.Role:
    role = MagicMock(spec=discord.Role)
    role.id = role_id
    role.mention = f"<@&{role_id}>"
    guild = MagicMock(spec=discord.Guild)
    guild.id = 1
    role.guild = guild
    return cast(discord.Role, role)


def make_member(
    *,
    member_id: int = 100,
    permissions: discord.Permissions | None = None,
    role_ids: tuple[int, ...] = (),
) -> discord.Member:
    member = MagicMock(spec=discord.Member)
    member.id = member_id
    member.guild_permissions = permissions if permissions is not None else make_permissions()
    member.roles = [make_role(role_id=rid) for rid in role_ids]
    return cast(discord.Member, member)


def make_text_channel(
    *,
    channel_id: int = 10,
    guild_id: int = 1,
    permissions: discord.Permissions | None = None,
) -> discord.TextChannel:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    guild = MagicMock(spec=discord.Guild)
    guild.id = guild_id
    channel.guild = guild
    channel.mention = f"<#{channel_id}>"
    channel.permissions_for = MagicMock(
        return_value=permissions if permissions is not None else make_permissions()
    )
    channel.send = AsyncMock(return_value=make_message())
    return cast(discord.TextChannel, channel)


def make_message(*, message_id: int = 999) -> discord.Message:
    message = MagicMock(spec=discord.Message)
    message.id = message_id
    message.jump_url = f"https://discord.com/channels/1/10/{message_id}"
    message.edit = AsyncMock()
    message.add_reaction = AsyncMock()
    message.reactions = []
    return cast(discord.Message, message)


def make_scheduled_event(
    *, event_id: int = 555, name: str = "Game Night"
) -> discord.ScheduledEvent:
    event = MagicMock(spec=discord.ScheduledEvent)
    event.id = event_id
    event.name = name
    return cast(discord.ScheduledEvent, event)


def make_guild(
    *,
    guild_id: int = 1,
    bot_member: discord.Member | None = None,
    role: discord.Role | None = None,
    channel: discord.TextChannel | None = None,
    scheduled_event: discord.ScheduledEvent | None = None,
) -> discord.Guild:
    guild = MagicMock(spec=discord.Guild)
    guild.id = guild_id
    guild.get_member = MagicMock(return_value=bot_member)
    guild.me = bot_member
    guild.get_role = MagicMock(return_value=role)
    guild.get_channel = MagicMock(return_value=channel)
    guild.emojis = []
    guild.create_scheduled_event = AsyncMock(
        return_value=scheduled_event if scheduled_event is not None else make_scheduled_event()
    )
    return cast(discord.Guild, guild)


@dataclass
class Ix:
    """An interaction plus its recording surfaces, for ergonomic assertions."""

    interaction: discord.Interaction
    response: AsyncMock
    followup: AsyncMock


def make_interaction(
    *,
    guild: discord.Guild | None = None,
    user: object | None = None,
    channel: object | None = None,
    message: discord.Message | None = None,
) -> Ix:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = guild
    interaction.user = user
    interaction.channel = channel
    interaction.message = message
    response = AsyncMock()
    followup = AsyncMock()
    interaction.response = response
    interaction.followup = followup
    return Ix(
        interaction=cast(discord.Interaction, interaction),
        response=response,
        followup=followup,
    )


def make_reaction(*, emoji: str, user_ids: tuple[int, ...]) -> MagicMock:
    """A reaction whose `.users()` yields members with the given ids."""

    async def _users() -> AsyncIterator[MagicMock]:
        for uid in user_ids:
            user = MagicMock()
            user.id = uid
            yield user

    reaction = MagicMock()
    reaction.emoji = emoji
    reaction.users = lambda: _users()
    return reaction


def make_raw_reaction(
    *,
    user_id: int,
    message_id: int,
    emoji: str,
    channel_id: int = 10,
) -> discord.RawReactionActionEvent:
    payload = MagicMock(spec=discord.RawReactionActionEvent)
    payload.user_id = user_id
    payload.message_id = message_id
    payload.channel_id = channel_id
    payload.emoji = emoji
    return cast(discord.RawReactionActionEvent, payload)


def sent_text(recorder: AsyncMock) -> str:
    """The first positional arg (message content) of the most recent await on `recorder`."""
    assert recorder.await_args is not None, "expected the recorder to have been awaited"
    return str(recorder.await_args.args[0])


async def invoke(command: Any, *args: Any, **kwargs: Any) -> None:
    """Call a slash-command's underlying callback (unwrapping the app_commands.Command)."""
    await command.callback(*args, **kwargs)
