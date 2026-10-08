from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

import discord

from butler.caches.events.recurrence_adapter import (
    RecurrenceRulePayload,
    fetch_raw_scheduled_events,
    recurrence_rules_from_raw_scheduled_events,
)
from butler.caches.events.reusability import event_sort_key, is_reusable_scheduled_event
from butler.timing import stopwatch

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScheduledEventListResult:
    """Result of listing guild scheduled events over HTTP."""

    events: list[discord.ScheduledEvent]
    recurrence_rules: dict[int, RecurrenceRulePayload]
    # False when every HTTP path failed (timeouts/connect). Empty list alone is OK.
    http_ok: bool


@dataclass(frozen=True)
class ReusableEventsLoad:
    """Reusable events plus whether Discord HTTP listing succeeded."""

    events: list[discord.ScheduledEvent]
    http_ok: bool

# Network blips (DNS/connect/timeouts) often surface as OSError/TimeoutError from
# aiohttp rather than discord.HTTPException. KeyError/TypeError/ValueError cover
# malformed raw payloads when hydrating discord.ScheduledEvent models.
_SCHEDULED_EVENT_LIST_ERRORS: tuple[type[BaseException], ...] = (
    discord.Forbidden,
    discord.HTTPException,
    OSError,
    TimeoutError,
    AttributeError,
    TypeError,
    ValueError,
    KeyError,
)
_SCHEDULED_EVENT_FETCH_ERRORS: tuple[type[BaseException], ...] = (
    discord.NotFound,
    discord.Forbidden,
    discord.HTTPException,
    OSError,
    TimeoutError,
)


def _hydrate_scheduled_events_from_raw(
    *,
    guild: discord.Guild,
    raw_events: Sequence[Mapping[str, object]],
) -> list[discord.ScheduledEvent]:
    """Build typed events from raw payloads; skip individual bad rows."""
    # discord.py connection state is needed to rebuild typed models.
    state = guild._state  # pyright: ignore[reportPrivateUsage]
    events: list[discord.ScheduledEvent] = []
    for raw_event in raw_events:
        try:
            events.append(
                discord.ScheduledEvent(state=state, data=cast(Any, raw_event))
            )
        except (AttributeError, KeyError, TypeError, ValueError):
            logger.warning(
                "Skipping malformed scheduled-event payload guild=%s payload_id=%r",
                guild.id,
                raw_event.get("id"),
                exc_info=True,
            )
    return events


async def list_scheduled_events_once(
    *,
    guild: discord.Guild,
) -> ScheduledEventListResult:
    """Prefer one HTTP list call for events + recurrence; fall back to typed fetch."""
    try:
        async with stopwatch() as elapsed:
            raw_events = await fetch_raw_scheduled_events(guild)
            listed: ScheduledEventListResult | None
            if raw_events:
                events = _hydrate_scheduled_events_from_raw(
                    guild=guild,
                    raw_events=raw_events,
                )
                if events:
                    listed = ScheduledEventListResult(
                        events=events,
                        recurrence_rules=recurrence_rules_from_raw_scheduled_events(
                            raw_events
                        ),
                        http_ok=True,
                    )
                else:
                    # Every payload failed to hydrate — try the public typed API.
                    listed = None
            else:
                listed = None
        logger.debug(
            "list_scheduled_events_http guild=%s took %.1fms",
            guild.id,
            elapsed.ms,
        )
        if listed is not None:
            return listed
    except _SCHEDULED_EVENT_LIST_ERRORS:
        logger.warning(
            "list_scheduled_events_http failed guild=%s", guild.id, exc_info=True
        )

    # Tests / degraded clients: single typed fetch, no second recurrence HTTP.
    try:
        async with stopwatch() as elapsed:
            events = await guild.fetch_scheduled_events(with_counts=False)
        logger.debug(
            "fetch_scheduled_events guild=%s took %.1fms",
            guild.id,
            elapsed.ms,
        )
    except _SCHEDULED_EVENT_LIST_ERRORS:
        logger.warning(
            "fetch_scheduled_events failed guild=%s", guild.id, exc_info=True
        )
        return ScheduledEventListResult(events=[], recurrence_rules={}, http_ok=False)
    return ScheduledEventListResult(
        events=list(events),
        recurrence_rules={},
        http_ok=True,
    )


async def reusable_scheduled_events_for_guild(
    *,
    guild: discord.Guild,
) -> list[discord.ScheduledEvent]:
    """Reusable events only (compat). Prefer ``load_reusable_events_for_guild``."""
    loaded = await load_reusable_events_for_guild(guild=guild)
    return loaded.events


async def load_reusable_events_for_guild(
    *,
    guild: discord.Guild,
) -> ReusableEventsLoad:
    """Load reusable events and whether Discord HTTP listing succeeded."""
    try:
        listed = await list_scheduled_events_once(guild=guild)
        events = listed.events
        recurrence_rules = listed.recurrence_rules
        http_ok = listed.http_ok
    except Exception:
        # Last-resort rescue so callers (warmup/resolve) never crash the bot loop.
        logger.exception(
            "list_scheduled_events_once raised unexpectedly guild=%s; using gateway cache",
            guild.id,
        )
        events, recurrence_rules, http_ok = [], {}, False

    if not events:
        gateway_events = list(guild.scheduled_events)
        if gateway_events:
            logger.debug(
                "reusable_scheduled_events guild=%s using gateway cache count=%s "
                "http_ok=%s",
                guild.id,
                len(gateway_events),
                http_ok,
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
    logger.debug(
        "reusable_scheduled_events guild=%s total=%s reusable=%s http_ok=%s",
        guild.id,
        len(events),
        len(reusable_events),
        http_ok,
    )
    return ReusableEventsLoad(events=reusable_events, http_ok=http_ok)


async def fetch_scheduled_event_by_id(
    *,
    guild: discord.Guild,
    event_id: int,
) -> discord.ScheduledEvent | None:
    cached = guild.get_scheduled_event(event_id)
    if cached is not None:
        return cached
    try:
        async with stopwatch() as elapsed:
            event = await guild.fetch_scheduled_event(event_id, with_counts=False)
        logger.debug(
            "fetch_scheduled_event id=%s guild=%s took %.1fms",
            event_id,
            guild.id,
            elapsed.ms,
        )
        return event
    except _SCHEDULED_EVENT_FETCH_ERRORS:
        return None
