from __future__ import annotations

import asyncio
import logging

import discord
from discord import Guild, app_commands

from butler.caches.events.constants import MAX_EVENT_AUTOCOMPLETE_CHOICES
from butler.caches.events.store import (
    AUTOCOMPLETE_EVENT_CACHE,
    event_option_cache_is_fresh,
)
from butler.design import (
    CREATE_NEW_EVENT_CHOICE_LABEL,
    CREATE_NEW_EVENT_CHOICE_VALUE,
    EVENT_AUTOCOMPLETE_ERROR_TEMPLATE,
)

logger = logging.getLogger(__name__)

_BACKGROUND_WARMUP_TASKS: set[asyncio.Task[None]] = set()


def create_new_choice() -> app_commands.Choice[str]:
    return app_commands.Choice(
        name=CREATE_NEW_EVENT_CHOICE_LABEL,
        value=CREATE_NEW_EVENT_CHOICE_VALUE,
    )


def schedule_background_warmup(guild: Guild) -> None:
    """Kick off a non-blocking warm if cache is cold (autocomplete must stay <3s)."""
    from butler.caches.events.warmup import warmup_connected_event_cache

    if event_option_cache_is_fresh(guild_id=guild.id):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(
        warmup_connected_event_cache(guilds=[guild], force=False),
        name=f"butler-event-cache-warmup-{guild.id}",
    )
    _BACKGROUND_WARMUP_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_WARMUP_TASKS.discard)


# NOSONAR - discord.py autocomplete callback is async by API contract.
async def autocomplete_existing_event(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Return cache-backed choices only. Never await Discord list here (3s limit)."""
    try:
        guild = interaction.guild
        if guild is None:
            return [create_new_choice()]

        # If cold, warm in background for later keystrokes — do not block this response.
        if not event_option_cache_is_fresh(guild_id=guild.id):
            schedule_background_warmup(guild)

        candidates = list(AUTOCOMPLETE_EVENT_CACHE.get(guild.id, []))
        query = current.strip().casefold()
        if query:
            candidates = [
                option
                for option in candidates
                if query in option[0].casefold() or query in option[1]
            ]
        event_choices = [
            app_commands.Choice(name=name, value=value)
            for name, value in candidates[: max(0, MAX_EVENT_AUTOCOMPLETE_CHOICES - 1)]
        ]
        return [create_new_choice(), *event_choices]
    except Exception as e:
        return [
            app_commands.Choice(
                name=EVENT_AUTOCOMPLETE_ERROR_TEMPLATE.format(error=repr(e)),
                value="fel",
            )
        ]
