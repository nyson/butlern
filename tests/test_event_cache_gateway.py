from __future__ import annotations

import datetime as dt
from typing import cast
from unittest.mock import MagicMock

import discord

from butler.caches import events as events_cache
from tests.discord_mocks import make_scheduled_event


def setup_function() -> None:
    events_cache.reset_connected_event_cache()


def _event_with_guild(
    *,
    event_id: int,
    guild_id: int,
    name: str = "Game",
    status: discord.EventStatus = discord.EventStatus.scheduled,
    start_time: dt.datetime | None = None,
) -> discord.ScheduledEvent:
    event = make_scheduled_event(
        event_id=event_id,
        name=name,
        status=status,
        start_time=start_time or (dt.datetime.now(dt.UTC) + dt.timedelta(hours=2)),
    )
    guild = MagicMock()
    guild.id = guild_id
    event.guild = guild
    event.guild_id = guild_id
    return cast(discord.ScheduledEvent, event)


def test_gateway_upsert_adds_reusable_event() -> None:
    event = _event_with_guild(event_id=10, guild_id=1, name="Tonight")
    events_cache.handle_gateway_scheduled_event_upsert(event, action="create")
    options = events_cache.AUTOCOMPLETE_EVENT_CACHE.get(1, [])
    assert any(value == "10" for _name, value in options)


def test_gateway_delete_removes_event() -> None:
    event = _event_with_guild(event_id=11, guild_id=2, name="Soon")
    events_cache.handle_gateway_scheduled_event_upsert(event, action="create")
    events_cache.handle_gateway_scheduled_event_delete(event)
    options = events_cache.AUTOCOMPLETE_EVENT_CACHE.get(2, [])
    assert all(value != "11" for _name, value in options)


def test_gateway_update_drops_completed_event() -> None:
    event = _event_with_guild(event_id=12, guild_id=3, name="Done")
    events_cache.handle_gateway_scheduled_event_upsert(event, action="create")
    event.status = discord.EventStatus.completed
    events_cache.handle_gateway_scheduled_event_upsert(event, action="update")
    options = events_cache.AUTOCOMPLETE_EVENT_CACHE.get(3, [])
    assert all(value != "12" for _name, value in options)
