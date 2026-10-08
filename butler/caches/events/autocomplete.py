from __future__ import annotations

import logging

import discord
from discord import app_commands

from butler.caches.events.constants import MAX_EVENT_AUTOCOMPLETE_CHOICES
from butler.caches.events.option_cache import (
    AUTOCOMPLETE_EVENT_CACHE,
    event_option_cache_is_fresh,
)
from butler.caches.events.warmup import schedule_event_cache_retry
from butler.design import (
    CREATE_NEW_EVENT_CHOICE_LABEL,
    CREATE_NEW_EVENT_CHOICE_VALUE,
    EVENT_AUTOCOMPLETE_ERROR_TEMPLATE,
)

logger = logging.getLogger(__name__)


class EventOptionCacheColdError(RuntimeError):
    """Raised when autocomplete runs while the guild event option cache is cold."""


def create_new_choice() -> app_commands.Choice[str]:
    return app_commands.Choice(
        name=CREATE_NEW_EVENT_CHOICE_LABEL,
        value=CREATE_NEW_EVENT_CHOICE_VALUE,
    )


def _schedule_cold_cache_retry(*, guild: discord.Guild) -> None:
    scheduled = schedule_event_cache_retry(guild=guild)
    if scheduled:
        logger.warning(
            "Event option cache cold for guild=%s; scheduled retry warm",
            guild.id,
        )
    else:
        logger.warning(
            "Event option cache cold for guild=%s; retry already pending",
            guild.id,
        )


def _cached_event_choices(
    *,
    guild: discord.Guild | None,
    current: str,
    include_create_new: bool,
) -> list[app_commands.Choice[str]]:
    if guild is None:
        return [create_new_choice()] if include_create_new else []

    guild_id = guild.id
    has_entries = guild_id in AUTOCOMPLETE_EVENT_CACHE
    fresh = event_option_cache_is_fresh(guild_id=guild_id)

    if not fresh:
        # Always try to repair drift / cold TTL in the background.
        _schedule_cold_cache_retry(guild=guild)
        if not has_entries:
            # Truly empty: no options to serve (link shows nothing; create keeps sentinel).
            raise EventOptionCacheColdError(
                f"Event option cache is cold for guild {guild_id}"
            )
        # Stale-while-revalidate: keep serving last options so /event link is usable
        # while a warm/retry is in flight (e.g. slow rehydrate HTTP).
        logger.debug(
            "Event option cache stale guild=%s; serving %s cached option(s) while refresh runs",
            guild_id,
            len(AUTOCOMPLETE_EVENT_CACHE.get(guild_id, [])),
        )

    candidates = list(AUTOCOMPLETE_EVENT_CACHE.get(guild_id, []))
    query = current.strip().casefold()
    if query:
        candidates = [
            option
            for option in candidates
            if query in option[0].casefold() or query in option[1]
        ]
    limit = MAX_EVENT_AUTOCOMPLETE_CHOICES
    if include_create_new:
        limit = max(0, MAX_EVENT_AUTOCOMPLETE_CHOICES - 1)
    event_choices = [
        app_commands.Choice(name=name, value=value)
        for name, value in candidates[:limit]
    ]
    logger.debug(
        "event autocomplete guild=%s fresh=%s query_len=%s options=%s include_create=%s",
        guild_id,
        fresh,
        len(query),
        len(event_choices),
        include_create_new,
    )
    if include_create_new:
        return [create_new_choice(), *event_choices]
    return event_choices


def _degraded_cold_choices(*, include_create_new: bool) -> list[app_commands.Choice[str]]:
    """Choices while cache is cold: create-new only when that path allows it."""
    if include_create_new:
        return [create_new_choice()]
    return []


# NOSONAR - discord.py autocomplete callback is async by API contract.
async def autocomplete_existing_event(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Return cache-backed existing-event choices (no create-new sentinel).

    Cold cache schedules a one-minute retry warm and returns no choices.
    """
    try:
        return _cached_event_choices(
            guild=interaction.guild,
            current=current,
            include_create_new=False,
        )
    except EventOptionCacheColdError:
        return _degraded_cold_choices(include_create_new=False)
    except Exception as e:
        return [
            app_commands.Choice(
                name=EVENT_AUTOCOMPLETE_ERROR_TEMPLATE.format(error=repr(e)),
                value="fel",
            )
        ]


# NOSONAR - discord.py autocomplete callback is async by API contract.
async def autocomplete_existing_or_create_event(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Cache-backed choices plus the create-new sentinel.

    Cold cache schedules a one-minute retry warm and still offers create-new.
    """
    try:
        return _cached_event_choices(
            guild=interaction.guild,
            current=current,
            include_create_new=True,
        )
    except EventOptionCacheColdError:
        return _degraded_cold_choices(include_create_new=True)
    except Exception as e:
        return [
            app_commands.Choice(
                name=EVENT_AUTOCOMPLETE_ERROR_TEMPLATE.format(error=repr(e)),
                value="fel",
            )
        ]
