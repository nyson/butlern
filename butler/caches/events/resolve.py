from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal

import discord

from butler.caches.events.listing import (
    fetch_scheduled_event_by_id,
    reusable_scheduled_events_for_guild,
)
from butler.caches.events.reusability import is_reusable_scheduled_event
from butler.caches.events.store import (
    cache_connected_event_id,
    cache_reusable_events_for_guild,
    cached_connected_event_id,
    clear_connected_event_id,
    drop_cached_event_option,
    upsert_cached_event_option,
)
from butler.design import CREATE_NEW_EVENT_CHOICE_VALUE
from butler.discord_events import coerce_utc

EventResolutionSource = Literal["selected", "cache", "lookup"]


@dataclass(frozen=True)
class ExistingEventResolution:
    event: discord.ScheduledEvent | None
    source: EventResolutionSource | None
    error_message: str | None = None


def parse_selected_event_id(value: str) -> int | None:
    normalized_value = value.strip()
    if not normalized_value:
        return None
    try:
        return int(normalized_value)
    except ValueError:
        return None


async def resolve_selected_existing_event(
    *,
    guild: discord.Guild,
    selected_event_value: str,
) -> ExistingEventResolution:
    event_id = parse_selected_event_id(selected_event_value)
    if event_id is None:
        return ExistingEventResolution(
            event=None,
            source=None,
            error_message="Invalid `existing_event` value. Pick an event from autocomplete.",
        )
    event = await fetch_scheduled_event_by_id(guild=guild, event_id=event_id)
    if event is None:
        return ExistingEventResolution(
            event=None,
            source=None,
            error_message=(
                "I couldn't find that selected event anymore. "
                "Pick it again from autocomplete."
            ),
        )
    if not is_reusable_scheduled_event(
        event=event,
        now_utc=dt.datetime.now(dt.UTC),
    ):
        drop_cached_event_option(guild_id=guild.id, event_id=event.id)
        return ExistingEventResolution(
            event=None,
            source=None,
            error_message="That selected event is no longer active/upcoming.",
        )
    cache_connected_event_id(guild_id=guild.id, event_id=event.id)
    upsert_cached_event_option(guild_id=guild.id, event=event)
    return ExistingEventResolution(event=event, source="selected")


async def resolve_cached_existing_event(
    *,
    guild: discord.Guild,
) -> discord.ScheduledEvent | None:
    cached_event_id = cached_connected_event_id(guild_id=guild.id)
    if cached_event_id is None:
        return None
    event = await fetch_scheduled_event_by_id(guild=guild, event_id=cached_event_id)
    if event is None:
        clear_connected_event_id(guild_id=guild.id)
        drop_cached_event_option(guild_id=guild.id, event_id=cached_event_id)
        return None
    if not is_reusable_scheduled_event(
        event=event,
        now_utc=dt.datetime.now(dt.UTC),
    ):
        clear_connected_event_id(guild_id=guild.id)
        drop_cached_event_option(guild_id=guild.id, event_id=cached_event_id)
        return None
    return event


async def resolve_existing_event_for_command(
    *,
    guild: discord.Guild,
    selected_event_value: str | None,
) -> ExistingEventResolution:
    if selected_event_value == CREATE_NEW_EVENT_CHOICE_VALUE:
        return ExistingEventResolution(event=None, source="selected")
    if selected_event_value is not None and selected_event_value.strip():
        return await resolve_selected_existing_event(
            guild=guild,
            selected_event_value=selected_event_value,
        )

    cached_event = await resolve_cached_existing_event(guild=guild)
    if cached_event is not None:
        return ExistingEventResolution(event=cached_event, source="cache")

    lookup_candidates = await reusable_scheduled_events_for_guild(guild=guild)
    cache_reusable_events_for_guild(
        guild_id=guild.id,
        events=lookup_candidates,
    )
    if not lookup_candidates:
        return ExistingEventResolution(event=None, source=None)
    selected_event = lookup_candidates[0]
    return ExistingEventResolution(event=selected_event, source="lookup")


def event_start_unix(
    *,
    event: discord.ScheduledEvent,
) -> int:
    return int(coerce_utc(event.start_time).timestamp())
