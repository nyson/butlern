from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from butler.discord_events import resolve_edition_media


def _emoji(*, name: str, emoji_id: int = 1) -> MagicMock:
    emoji = MagicMock(spec=discord.Emoji)
    emoji.name = name
    emoji.id = emoji_id
    emoji.url = f"https://cdn.discordapp.com/emojis/{emoji_id}.png"
    emoji.configure_mock(**{"__str__.return_value": f"<:{name}:{emoji_id}>"})
    return emoji


def _guild_with_emojis(*names: str) -> discord.Guild:
    guild = MagicMock(spec=discord.Guild)
    emojis = [_emoji(name=name, emoji_id=index + 1) for index, name in enumerate(names)]
    guild.emojis = emojis
    guild.fetch_emojis = AsyncMock(return_value=emojis)
    return guild


@pytest.mark.asyncio
async def test_resolve_edition_media_uses_full_guild_emoji_names() -> None:
    guild = _guild_with_emojis(
        "custom",
        "trouble_brewing",
        "bad_moon_rising",
        "sects_and_violets",
    )

    cases = (
        ("Custom", "custom"),
        ("Trouble Brewing", "trouble_brewing"),
        ("trouble brewing", "trouble_brewing"),
        ("Bad Moon Rising", "bad_moon_rising"),
        ("Sects and Violets", "sects_and_violets"),
        # Official abbreviations are edition lookup keys; prefer full emoji names.
        ("tb", "trouble_brewing"),
        ("bmr", "bad_moon_rising"),
        ("snv", "sects_and_violets"),
        # Unknown editions fall through to custom.
        ("Some Homebrew", "custom"),
    )
    for edition, expected_name in cases:
        rendered, image_url = await resolve_edition_media(guild=guild, edition=edition)
        assert rendered is not None, edition
        assert expected_name in rendered, edition
        assert image_url is not None
        assert image_url.endswith(".png")


@pytest.mark.asyncio
async def test_resolve_edition_media_accepts_short_guild_emoji_names() -> None:
    """Guilds may host abbrev emoji names (bmr/snv/tb) instead of full forms."""
    guild = _guild_with_emojis("custom", "tb", "bmr", "snv", "logo")

    cases = (
        ("Trouble Brewing", "tb"),
        ("tb", "tb"),
        ("Bad Moon Rising", "bmr"),
        ("bmr", "bmr"),
        ("Sects and Violets", "snv"),
        ("snv", "snv"),
        ("Custom", "custom"),
        ("weird script", "custom"),
    )
    for edition, expected_name in cases:
        rendered, image_url = await resolve_edition_media(guild=guild, edition=edition)
        assert rendered is not None, edition
        assert expected_name in rendered, (edition, rendered)
        assert image_url is not None


@pytest.mark.asyncio
async def test_resolve_edition_media_falls_back_to_custom_when_edition_missing() -> None:
    guild = _guild_with_emojis("custom")
    rendered, image_url = await resolve_edition_media(guild=guild, edition="Trouble Brewing")
    assert rendered == "<:custom:1>"
    assert image_url == "https://cdn.discordapp.com/emojis/1.png"


@pytest.mark.asyncio
async def test_resolve_edition_media_returns_none_when_no_emojis() -> None:
    guild = _guild_with_emojis()
    assert await resolve_edition_media(guild=guild, edition="Trouble Brewing") == (None, None)


@pytest.mark.asyncio
async def test_resolve_edition_media_uses_fetch_over_stale_cache() -> None:
    guild = MagicMock(spec=discord.Guild)
    guild.emojis = [_emoji(name="custom", emoji_id=1)]
    guild.fetch_emojis = AsyncMock(
        return_value=[
            _emoji(name="trouble_brewing", emoji_id=9),
            _emoji(name="custom", emoji_id=1),
        ]
    )
    rendered, image_url = await resolve_edition_media(guild=guild, edition="Trouble Brewing")
    assert rendered == "<:trouble_brewing:9>"
    assert image_url == "https://cdn.discordapp.com/emojis/9.png"
