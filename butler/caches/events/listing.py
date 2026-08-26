from __future__ import annotations

import datetime as dt
import logging

import discord

from butler.caches.events.recurrence_adapter import (
    RecurrenceRulePayload,
    fetch_raw_scheduled_events,
    recurrence_rules_from_raw_scheduled_events,
)
from butler.caches.events.reusability import event_sort_key, is_reusable_scheduled_event
from butler.timing import stopwatch

logger = logging.getLogger(__name__)


async def list_scheduled_events_once(
    *,
    guild: discord.Guild,
) -> tuple[list[discord.ScheduledEvent], dict[int, RecurrenceRulePayload]]:
    """Prefer one HTTP list call for events + recurrence; fall back to typed fetch."""
    try:
        async with stopwatch("list_scheduled_events_http", guild_id=guild.id):
            raw_events = await fetch_raw_scheduled_events(guild)
            if raw_events:
                # discord.py connection state is needed to rebuild typed models.
                state = guild._state
                events = [
                    discord.ScheduledEvent(state=state, data=raw_event)  # type: ignore[arg-type]
                    for raw_event in raw_events
                ]
                return events, recurrence_rules_from_raw_scheduled_events(raw_events)
    except (discord.Forbidden, discord.HTTPException, AttributeError, TypeError, ValueError):
        logger.warning(
            "list_scheduled_events_http failed guild=%s", guild.id, exc_info=True
        )

    # Tests / degraded clients: single typed fetch, no second recurrence HTTP.
    try:
        async with stopwatch("fetch_scheduled_events", guild_id=guild.id):
            events = await guild.fetch_scheduled_events(with_counts=False)
    except (discord.Forbidden, discord.HTTPException):
        logger.warning(
            "fetch_scheduled_events failed guild=%s", guild.id, exc_info=True
        )
        return [], {}
    return list(events), {}


async def reusable_scheduled_events_for_guild(
    *,
    guild: discord.Guild,
) -> list[discord.ScheduledEvent]:
    events, recurrence_rules = await list_scheduled_events_once(guild=guild)
    if not events:
        gateway_events = list(guild.scheduled_events)
        if gateway_events:
            logger.info(
                "reusable_scheduled_events guild=%s using gateway cache count=%s",
                guild.id,
                len(gateway_events),
            )
            events = gateway_events
            recurrence_rules = {}

    now_utc = dt.datetime.now(dt.UTC)
    reusable_events = [
        event
        for event in events
        if is_reusable_scheduled_event(
            event=event,
            now_utc=now_utc,
            recurrence_rule=recurrence_rules.get(event.id),
        )
    ]
    reusable_events.sort(key=event_sort_key)
    logger.info(
        "reusable_scheduled_events guild=%s total=%s reusable=%s",
        guild.id,
        len(events),
        len(reusable_events),
    )
    return reusable_events


async def fetch_scheduled_event_by_id(
    *,
    guild: discord.Guild,
    event_id: int,
) -> discord.ScheduledEvent | None:
    cached = guild.get_scheduled_event(event_id)
    if cached is not None:
        return cached
    try:
        async with stopwatch(
            f"fetch_scheduled_event id={event_id}",
            guild_id=guild.id,
        ):
            return await guild.fetch_scheduled_event(event_id, with_counts=False)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None
