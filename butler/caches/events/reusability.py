from __future__ import annotations

import datetime as dt
from collections.abc import Mapping

import discord

from butler.caches.events.constants import MAX_EVENT_CHOICE_NAME_LENGTH
from butler.caches.events.recurrence import (
    occurrence_start_utc_for_local_date,
    recurrence_rule_payload_for_event,
)
from butler.constants import SWEDISH_TIMEZONE
from butler.discord_events import event_start_utc


def is_reusable_scheduled_event(
    *,
    event: discord.ScheduledEvent,
    now_utc: dt.datetime,
    recurrence_rule: Mapping[str, object] | None = None,
) -> bool:
    if event.status not in {discord.EventStatus.active, discord.EventStatus.scheduled}:
        return False
    start_utc = event_start_utc(event)
    now_swedish = now_utc.astimezone(SWEDISH_TIMEZONE)
    occurrence_start = occurrence_start_utc_for_local_date(
        event_start_utc=start_utc,
        target_local_date=now_swedish.date(),
        local_timezone=SWEDISH_TIMEZONE,
        recurrence_rule=(
            recurrence_rule
            if recurrence_rule is not None
            else recurrence_rule_payload_for_event(event)
        ),
    )
    if occurrence_start is None:
        return False
    return not (
        event.status == discord.EventStatus.scheduled and occurrence_start < now_utc
    )


def event_sort_key(event: discord.ScheduledEvent) -> tuple[int, float, int]:
    status_priority = 0 if event.status == discord.EventStatus.active else 1
    start_timestamp = event_start_utc(event).timestamp()
    return status_priority, start_timestamp, event.id


def event_choice_name(event: discord.ScheduledEvent) -> str:
    swedish_start = event_start_utc(event).astimezone(SWEDISH_TIMEZONE)
    start_label = swedish_start.strftime("%H:%M")
    raw_name = f"{event.name} — {start_label}"
    if len(raw_name) <= MAX_EVENT_CHOICE_NAME_LENGTH:
        return raw_name
    return f"{raw_name[:MAX_EVENT_CHOICE_NAME_LENGTH - 1]}…"
