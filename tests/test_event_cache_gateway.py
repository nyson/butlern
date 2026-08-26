from __future__ import annotations

import datetime as dt

import discord

from butler.caches import events as events_cache
from butler.caches.events.option_cache import AUTOCOMPLETE_EVENT_CACHE, upsert_cached_event_option
from tests.discord_mocks import make_scheduled_event

# Fixed instants only — gateway reusability uses wall-clock `datetime.now`, so
# "is this event reusable right now?" is not covered here (see arch.md).
_STATIC_START = dt.datetime(2026, 6, 15, 18, 0, tzinfo=dt.UTC)


def setup_function() -> None:
    events_cache.reset_connected_event_cache()


def _event_with_guild(
    *,
    event_id: int,
    guild_id: int,
    name: str = "Game",
    status: discord.EventStatus = discord.EventStatus.scheduled,
    start_time: dt.datetime = _STATIC_START,
) -> discord.ScheduledEvent:
    return make_scheduled_event(
        event_id=event_id,
        name=name,
        status=status,
        start_time=start_time,
        guild_id=guild_id,
    )


def _seed_cached_option(*, guild_id: int, event: discord.ScheduledEvent) -> None:
    upsert_cached_event_option(guild_id=guild_id, event=event)


def test_gateway_delete_removes_event() -> None:
    event = _event_with_guild(event_id=11, guild_id=2, name="Soon")
    _seed_cached_option(guild_id=2, event=event)

    events_cache.handle_gateway_scheduled_event_delete(event)

    options = AUTOCOMPLETE_EVENT_CACHE.get(2, [])
    assert all(value != "11" for _name, value in options)


def test_gateway_update_drops_completed_event() -> None:
    event = _event_with_guild(event_id=12, guild_id=3, name="Done")
    _seed_cached_option(guild_id=3, event=event)
    event.status = discord.EventStatus.completed

    events_cache.handle_gateway_scheduled_event_upsert(event, action="update")

    options = AUTOCOMPLETE_EVENT_CACHE.get(3, [])
    assert all(value != "12" for _name, value in options)
