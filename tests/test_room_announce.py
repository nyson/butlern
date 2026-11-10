from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import discord

from butler.rsvp.room_announce import (
    announce_room_opening,
    build_room_opened_content,
    build_user_mentions,
    find_player_role,
)
from tests.discord_mocks import make_interaction, make_role, make_text_channel


def test_build_user_mentions_dedupes_and_sorts() -> None:
    assert build_user_mentions([3, 1, 3, 2]) == "<@1> <@2> <@3>"


def test_build_room_opened_content_format() -> None:
    content = build_room_opened_content(
        user_ids=(11, 7),
        room_url="https://botc.app/join/demo",
        optional_message="Vi startar snart",
    )
    assert content == (
        "Hej <@7> <@11>!\n"
        "\n"
        "Rummet är öppet: https://botc.app/join/demo\n"
        "\n"
        "Vi startar snart"
    )


def test_build_room_opened_content_without_users_or_message() -> None:
    content = build_room_opened_content(
        user_ids=(),
        room_url="https://botc.app/join/demo",
    )
    assert content == (
        "Hej!\n"
        "\n"
        "Rummet är öppet: https://botc.app/join/demo"
    )


async def test_announce_uses_configured_channel_when_provided() -> None:
    interaction_channel = make_text_channel(channel_id=1)
    announcement_channel = make_text_channel(channel_id=2)
    announce_send = AsyncMock()
    interaction_send = AsyncMock()
    cast(Any, announcement_channel).send = announce_send
    cast(Any, interaction_channel).send = interaction_send
    ix = make_interaction(channel=interaction_channel)

    await announce_room_opening(
        interaction=ix.interaction,
        user_ids=(11,),
        room_url="https://botc.app/join/demo",
        announcement_channel=announcement_channel,
    )

    announce_send.assert_awaited_once()
    interaction_send.assert_not_awaited()
    assert announce_send.await_args is not None
    content = announce_send.await_args.args[0]
    assert content.startswith("Hej <@11>!")
    assert "Rummet är öppet: https://botc.app/join/demo" in content
    assert "](https://botc.app/join/demo)" not in content
    assert announce_send.await_args.kwargs.get("suppress_embeds") is True


async def test_announce_falls_back_to_interaction_channel() -> None:
    channel = make_text_channel(channel_id=5)
    channel_send = AsyncMock()
    cast(Any, channel).send = channel_send
    ix = make_interaction(channel=channel)

    await announce_room_opening(
        interaction=ix.interaction,
        user_ids=(),
        room_url="https://botc.app/join/demo",
    )

    channel_send.assert_awaited_once()
    assert channel_send.await_args is not None
    content = channel_send.await_args.args[0]
    assert isinstance(content, str)
    assert content == (
        "Hej!\n"
        "\n"
        "Rummet är öppet: https://botc.app/join/demo"
    )


async def test_announce_includes_optional_message() -> None:
    channel = make_text_channel(channel_id=5)
    channel_send = AsyncMock()
    cast(Any, channel).send = channel_send
    ix = make_interaction(channel=channel)

    await announce_room_opening(
        interaction=ix.interaction,
        user_ids=(7,),
        room_url="https://botc.app/join/demo",
        optional_message="  Vi startar snart  ",
    )

    channel_send.assert_awaited_once()
    assert channel_send.await_args is not None
    content = channel_send.await_args.args[0]
    assert content == (
        "Hej <@7>!\n"
        "\n"
        "Rummet är öppet: https://botc.app/join/demo\n"
        "\n"
        "Vi startar snart"
    )


async def test_announce_without_pings_still_posts() -> None:
    channel = make_text_channel(channel_id=5)
    channel_send = AsyncMock()
    cast(Any, channel).send = channel_send
    ix = make_interaction(channel=channel)

    await announce_room_opening(
        interaction=ix.interaction,
        room_url="https://botc.app/join/demo",
    )

    channel_send.assert_awaited_once()
    assert channel_send.await_args is not None
    assert channel_send.await_args.args[0].startswith("Hej!")


async def test_announce_mentions_role_instead_of_users() -> None:
    channel = make_text_channel(channel_id=5)
    channel_send = AsyncMock()
    cast(Any, channel).send = channel_send
    ix = make_interaction(channel=channel)
    role = make_role(role_id=99)

    await announce_room_opening(
        interaction=ix.interaction,
        user_ids=(1,),
        role=role,
        room_url="https://botc.app/join/demo",
    )

    assert channel_send.await_args is not None
    assert channel_send.await_args.args[0].startswith("Hej <@&99>!")
    allowed = channel_send.await_args.kwargs["allowed_mentions"]
    assert allowed.roles == [role]


def test_find_player_role_matches_name_case_insensitively() -> None:
    other = MagicMock(spec=discord.Role)
    other.name = "Storyteller"
    players = MagicMock(spec=discord.Role)
    players.name = "spelare"
    guild = MagicMock(spec=discord.Guild)
    guild.roles = [other, players]
    assert find_player_role(cast(discord.Guild, guild)) is players
    guild.roles = [other]
    assert find_player_role(cast(discord.Guild, guild)) is None


async def test_announce_skips_non_text_channels() -> None:
    channel = MagicMock(spec=discord.VoiceChannel)
    channel_send = AsyncMock()
    cast(Any, channel).send = channel_send
    ix = make_interaction(channel=channel)

    await announce_room_opening(
        interaction=ix.interaction,
        user_ids=(1,),
        room_url="https://botc.app/join/demo",
    )

    channel_send.assert_not_awaited()
