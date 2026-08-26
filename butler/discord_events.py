from __future__ import annotations

import datetime as dt
import logging
from typing import Final

import discord
from discord import app_commands

from butler.constants import DEFAULT_EVENT_LOCATION
from butler.design import EDITION_EMOJI_NAME_CANDIDATES, EDITION_RESOURCE_ID_BY_NAME
from butler.event_logic import EventInput

logger = logging.getLogger(__name__)

BOTC_EDITIONS: Final[tuple[str, ...]] = (
    "Trouble Brewing",
    "Bad Moon Rising",
    "Sects and Violets",
    "Custom",
)
BOTC_EDITION_CHOICES: Final[list[app_commands.Choice[str]]] = [
    app_commands.Choice(name=edition, value=edition)
    for edition in BOTC_EDITIONS
]

BOT_PERMISSION_VERIFY_FAILURE_MESSAGE: Final[str] = (
    "I couldn't verify my server permissions. Re-invite the bot and try again."
)


def build_event_url(*, guild_id: int, event_id: int) -> str:
    return f"https://discord.com/events/{guild_id}/{event_id}"


def build_preview_event_url(*, guild_id: int, channel_id: int) -> str:
    return f"https://discord.com/channels/{guild_id}/{channel_id}"


def coerce_utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC)


def event_start_utc(event: discord.ScheduledEvent) -> dt.datetime:
    return coerce_utc(event.start_time)


def event_start_unix(*, event: discord.ScheduledEvent) -> int:
    return int(coerce_utc(event.start_time).timestamp())


def _emoji_by_name(emojis: list[discord.Emoji], name: str) -> discord.Emoji | None:
    target = name.casefold()
    for emoji in emojis:
        if emoji.name.casefold() == target:
            return emoji
    return None


def _edition_resource_id(edition: str) -> str:
    """Map display name / short code to canonical resource id (case-insensitive).

    Unknown editions fall through to ``custom`` so cosmetic media still resolves.
    """
    return EDITION_RESOURCE_ID_BY_NAME.get(edition.casefold().strip(), "custom")


async def resolve_edition_media(
    *,
    guild: discord.Guild,
    edition: str | None,
) -> tuple[str | None, str | None]:
    """Resolve guild custom emoji + image URL for an edition name.

    Uses ``EDITION_RESOURCE_ID_BY_NAME`` (Trouble Brewing / tb → ``trouble_brewing``).
    Tries each name in ``EDITION_EMOJI_NAME_CANDIDATES`` so guilds can host either
    full names (``trouble_brewing``) or short codes (``tb`` / ``bmr`` / ``snv``).
    Prefers a live ``fetch_emojis()`` list so cold guild caches do not miss assets,
    then falls back to the cached ``guild.emojis``. Missing specific edition emojis
    may fall back to ``custom``.
    """
    if edition is None:
        logger.info(
            "resolve_edition_media guild_id=%s edition=None -> no media",
            guild.id,
        )
        return None, None

    edition_resource_id = _edition_resource_id(edition)

    emojis: list[discord.Emoji]
    emoji_source = "fetch_emojis"
    try:
        emojis = list(await guild.fetch_emojis())
    except (discord.HTTPException, discord.Forbidden) as exc:
        emojis = list(guild.emojis)
        emoji_source = f"guild.emojis_cache ({type(exc).__name__})"

    emoji_names = sorted({emoji.name for emoji in emojis})
    candidates = EDITION_EMOJI_NAME_CANDIDATES.get(
        edition_resource_id,
        (edition_resource_id,),
    )
    emoji: discord.Emoji | None = None
    matched_via: str | None = None
    for candidate in candidates:
        emoji = _emoji_by_name(emojis, candidate)
        if emoji is not None:
            matched_via = candidate
            break

    used_fallback = False
    if emoji is None and edition_resource_id != "custom":
        emoji = _emoji_by_name(emojis, "custom")
        used_fallback = emoji is not None
        if used_fallback:
            matched_via = "custom"
    if emoji is None:
        logger.info(
            "resolve_edition_media guild_id=%s edition=%r resource_id=%r "
            "source=%s emoji_count=%s candidates=%s emoji_names=%s "
            "match=None fallback=False",
            guild.id,
            edition,
            edition_resource_id,
            emoji_source,
            len(emojis),
            list(candidates),
            emoji_names,
        )
        return None, None

    rendered = str(emoji)
    image_url = str(emoji.url)
    logger.info(
        "resolve_edition_media guild_id=%s edition=%r resource_id=%r "
        "source=%s emoji_count=%s matched_name=%r matched_via=%r matched_id=%s "
        "fallback_to_custom=%s rendered=%r image_url=%r emoji_names=%s",
        guild.id,
        edition,
        edition_resource_id,
        emoji_source,
        len(emojis),
        emoji.name,
        matched_via,
        emoji.id,
        used_fallback,
        rendered,
        image_url,
        emoji_names,
    )
    return rendered, image_url


async def create_scheduled_event(
    *,
    interaction: discord.Interaction,
    guild: discord.Guild,
    event_input: EventInput,
) -> discord.ScheduledEvent | None:
    try:
        return await guild.create_scheduled_event(
            name=event_input.title,
            start_time=event_input.start_utc,
            end_time=event_input.end_utc,
            entity_type=discord.EntityType.external,
            privacy_level=discord.PrivacyLevel.guild_only,
            description=event_input.description,
            location=DEFAULT_EVENT_LOCATION,
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "I can't create this scheduled event. Check bot permissions (`Create Events`).",
            ephemeral=True,
        )
        return None
    except discord.HTTPException as exc:
        await interaction.followup.send(
            f"Discord rejected event creation: {exc}",
            ephemeral=True,
        )
        return None
