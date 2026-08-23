from __future__ import annotations

import time

import discord

from butler.caches.events.constants import EVENT_OPTION_CACHE_TTL_SECONDS
from butler.caches.events.reusability import event_choice_name

# In-memory process caches (not persisted).
CONNECTED_EVENT_CACHE: dict[int, int] = {}
AUTOCOMPLETE_EVENT_CACHE: dict[int, list[tuple[str, str]]] = {}
AUTOCOMPLETE_EVENT_CACHE_AT: dict[int, float] = {}


def reset_connected_event_cache() -> None:
    CONNECTED_EVENT_CACHE.clear()
    AUTOCOMPLETE_EVENT_CACHE.clear()
    AUTOCOMPLETE_EVENT_CACHE_AT.clear()


def cached_connected_event_id(*, guild_id: int) -> int | None:
    return CONNECTED_EVENT_CACHE.get(guild_id)


def cache_connected_event_id(*, guild_id: int, event_id: int) -> None:
    CONNECTED_EVENT_CACHE[guild_id] = event_id


def clear_connected_event_id(*, guild_id: int) -> None:
    CONNECTED_EVENT_CACHE.pop(guild_id, None)


def drop_cached_event_option(*, guild_id: int, event_id: int) -> None:
    event_id_value = str(event_id)
    cached_options = AUTOCOMPLETE_EVENT_CACHE.get(guild_id, [])
    AUTOCOMPLETE_EVENT_CACHE[guild_id] = [
        option for option in cached_options if option[1] != event_id_value
    ]


def invalidate_event_option_cache(*, guild_id: int) -> None:
    AUTOCOMPLETE_EVENT_CACHE.pop(guild_id, None)
    AUTOCOMPLETE_EVENT_CACHE_AT.pop(guild_id, None)


def event_option_cache_is_fresh(*, guild_id: int) -> bool:
    cached_at = AUTOCOMPLETE_EVENT_CACHE_AT.get(guild_id)
    if cached_at is None or guild_id not in AUTOCOMPLETE_EVENT_CACHE:
        return False
    return (time.monotonic() - cached_at) < EVENT_OPTION_CACHE_TTL_SECONDS


def upsert_cached_event_option(*, guild_id: int, event: discord.ScheduledEvent) -> None:
    event_id_value = str(event.id)
    existing_options = AUTOCOMPLETE_EVENT_CACHE.get(guild_id, [])
    updated_options: list[tuple[str, str]] = [(event_choice_name(event), event_id_value)]
    updated_options.extend(
        option for option in existing_options if option[1] != event_id_value
    )
    AUTOCOMPLETE_EVENT_CACHE[guild_id] = updated_options
    AUTOCOMPLETE_EVENT_CACHE_AT[guild_id] = time.monotonic()


def cache_reusable_events_for_guild(
    *,
    guild_id: int,
    events: list[discord.ScheduledEvent],
) -> None:
    if not events:
        clear_connected_event_id(guild_id=guild_id)
        AUTOCOMPLETE_EVENT_CACHE[guild_id] = []
        AUTOCOMPLETE_EVENT_CACHE_AT[guild_id] = time.monotonic()
        return
    cache_connected_event_id(guild_id=guild_id, event_id=events[0].id)
    AUTOCOMPLETE_EVENT_CACHE[guild_id] = [
        (event_choice_name(event), str(event.id)) for event in events
    ]
    AUTOCOMPLETE_EVENT_CACHE_AT[guild_id] = time.monotonic()


def touch_cache_ttl(*, guild_id: int) -> None:
    """Refresh TTL stamp without changing option contents."""
    if guild_id in AUTOCOMPLETE_EVENT_CACHE:
        AUTOCOMPLETE_EVENT_CACHE_AT[guild_id] = time.monotonic()
