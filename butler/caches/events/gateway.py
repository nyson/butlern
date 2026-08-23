from __future__ import annotations

import datetime as dt
import logging
from typing import Literal

import discord

from butler.caches.events.constants import SWEDISH_TIMEZONE
from butler.caches.events.recurrence import recurrence_rule_payload_for_event
from butler.caches.events.reusability import is_reusable_scheduled_event
from butler.caches.events.store import (
    AUTOCOMPLETE_EVENT_CACHE,
    cached_connected_event_id,
    clear_connected_event_id,
    drop_cached_event_option,
    touch_cache_ttl,
    upsert_cached_event_option,
)
from butler.discord_events import coerce_utc

logger = logging.getLogger(__name__)


def guild_id_from_scheduled_event(event: discord.ScheduledEvent) -> int | None:
    guild = event.guild
    if guild is not None:
        return guild.id
    guild_id = event.guild_id
    return guild_id if isinstance(guild_id, int) else None


def event_status_name(event: discord.ScheduledEvent) -> str:
    status = event.status
    return status.name if status is not None else "unknown"


def event_start_label(event: discord.ScheduledEvent) -> str:
    start = event.start_time
    try:
        return coerce_utc(start).astimezone(SWEDISH_TIMEZONE).strftime("%Y-%m-%d %H:%M %Z")
    except (OSError, OverflowError, TypeError, ValueError):
        return repr(start)


def event_log_fields(event: discord.ScheduledEvent) -> dict[str, object]:
    return {
        "event_id": event.id,
        "name": event.name,
        "status": event_status_name(event),
        "start": event_start_label(event),
        "guild_id": guild_id_from_scheduled_event(event),
    }


def handle_gateway_scheduled_event_upsert(
    event: discord.ScheduledEvent,
    *,
    action: Literal["create", "update"] = "update",
) -> None:
    """Apply create/update from Discord gateway without listing the guild."""
    fields = event_log_fields(event)
    guild_id = fields["guild_id"]
    if not isinstance(guild_id, int):
        logger.warning(
            "Discord scheduled_event.%s ignored (missing guild id): event_id=%s name=%r",
            action,
            fields["event_id"],
            fields["name"],
        )
        return

    was_cached = any(
        value == str(event.id)
        for _label, value in AUTOCOMPLETE_EVENT_CACHE.get(guild_id, [])
    )
    recurrence = recurrence_rule_payload_for_event(event)
    reusable = is_reusable_scheduled_event(
        event=event,
        now_utc=dt.datetime.now(dt.UTC),
        recurrence_rule=recurrence,
    )
    logger.info(
        "Discord scheduled_event.%s guild=%s event_id=%s name=%r status=%s start=%s "
        "reusable=%s was_cached=%s",
        action,
        guild_id,
        fields["event_id"],
        fields["name"],
        fields["status"],
        fields["start"],
        reusable,
        was_cached,
    )

    if reusable:
        upsert_cached_event_option(guild_id=guild_id, event=event)
        logger.info(
            "Event cache %s guild=%s event_id=%s name=%r (options=%s)",
            "updated" if was_cached else "added",
            guild_id,
            event.id,
            event.name,
            len(AUTOCOMPLETE_EVENT_CACHE.get(guild_id, [])),
        )
        return

    drop_cached_event_option(guild_id=guild_id, event_id=event.id)
    if cached_connected_event_id(guild_id=guild_id) == event.id:
        clear_connected_event_id(guild_id=guild_id)
        logger.info(
            "Event cache cleared connected event guild=%s event_id=%s",
            guild_id,
            event.id,
        )
    # Keep TTL stamp so we do not immediately re-list after a non-reusable update.
    touch_cache_ttl(guild_id=guild_id)
    logger.info(
        "Event cache removed non-reusable guild=%s event_id=%s name=%r status=%s "
        "was_cached=%s options=%s",
        guild_id,
        event.id,
        event.name,
        fields["status"],
        was_cached,
        len(AUTOCOMPLETE_EVENT_CACHE.get(guild_id, [])),
    )


def handle_gateway_scheduled_event_delete(event: discord.ScheduledEvent) -> None:
    """Apply delete from Discord gateway without listing the guild."""
    fields = event_log_fields(event)
    guild_id = fields["guild_id"]
    if not isinstance(guild_id, int):
        logger.warning(
            "Discord scheduled_event.delete ignored (missing guild id): event_id=%s name=%r",
            fields["event_id"],
            fields["name"],
        )
        return

    was_cached = any(
        value == str(event.id)
        for _label, value in AUTOCOMPLETE_EVENT_CACHE.get(guild_id, [])
    )
    logger.info(
        "Discord scheduled_event.delete guild=%s event_id=%s name=%r status=%s "
        "start=%s was_cached=%s",
        guild_id,
        fields["event_id"],
        fields["name"],
        fields["status"],
        fields["start"],
        was_cached,
    )

    drop_cached_event_option(guild_id=guild_id, event_id=event.id)
    if cached_connected_event_id(guild_id=guild_id) == event.id:
        clear_connected_event_id(guild_id=guild_id)
        logger.info(
            "Event cache cleared connected event guild=%s event_id=%s",
            guild_id,
            event.id,
        )
    touch_cache_ttl(guild_id=guild_id)
    logger.info(
        "Event cache removed deleted guild=%s event_id=%s was_cached=%s options=%s",
        guild_id,
        event.id,
        was_cached,
        len(AUTOCOMPLETE_EVENT_CACHE.get(guild_id, [])),
    )
