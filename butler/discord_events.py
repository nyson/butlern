from __future__ import annotations

import datetime as dt
from typing import Final

import discord
from discord import app_commands

from butler.constants import DEFAULT_EVENT_LOCATION
from butler.design import EDITION_RESOURCE_ID_BY_NAME
from butler.event_logic import EventInput

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


def resolve_edition_media(
    *,
    guild: discord.Guild,
    edition: str | None,
) -> tuple[str | None, str | None]:
    if edition is None:
        return None, None

    edition_resource_id = EDITION_RESOURCE_ID_BY_NAME.get(edition)
    if edition_resource_id is None:
        return None, None

    emoji = discord.utils.get(guild.emojis, name=edition_resource_id)
    if emoji is None and edition_resource_id != "custom":
        emoji = discord.utils.get(guild.emojis, name="custom")
    if emoji is None:
        return None, None
    return str(emoji), str(emoji.url)


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
