from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from typing import cast

import discord

from butler.discord_events import coerce_utc


def parse_weekdays(value: object) -> set[int] | None:
    if not isinstance(value, list):
        return None
    if not value:
        return set()
    parsed: set[int] = set()
    weekday_values = cast(list[object], value)
    for weekday_value in weekday_values:
        if not isinstance(weekday_value, int):
            return None
        if weekday_value < 0 or weekday_value > 6:
            return None
        parsed.add(weekday_value)
    return parsed


def parse_positive_int(value: object, *, default: int) -> int | None:
    if value is None:
        return default
    if not isinstance(value, int):
        return None
    if value < 1:
        return None
    return value


def parse_date_value(value: object) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return coerce_utc(value).date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return coerce_utc(parsed).date()
    return None


def is_weekly_frequency(value: object) -> bool:
    if isinstance(value, str):
        return value.casefold() == "weekly"
    return value == 2


def allowed_weekdays_for_weekly_rule(
    *,
    recurrence_rule: Mapping[str, object],
    anchor_weekday: int,
) -> set[int] | None:
    if "by_weekday" not in recurrence_rule:
        return {anchor_weekday}
    weekdays = parse_weekdays(recurrence_rule.get("by_weekday"))
    if weekdays is None or not weekdays:
        return None
    return weekdays


def weekly_rule_matches_target_date(
    *,
    recurrence_rule: Mapping[str, object],
    anchor_date: dt.date,
    target_local_date: dt.date,
) -> bool:
    if target_local_date < anchor_date:
        return False

    interval = parse_positive_int(recurrence_rule.get("interval"), default=1)
    if interval is None:
        return False

    allowed_weekdays = allowed_weekdays_for_weekly_rule(
        recurrence_rule=recurrence_rule,
        anchor_weekday=anchor_date.weekday(),
    )
    if allowed_weekdays is None:
        return False
    if target_local_date.weekday() not in allowed_weekdays:
        return False

    end_date = parse_date_value(recurrence_rule.get("end"))
    if end_date is not None and target_local_date > end_date:
        return False

    days_delta = (target_local_date - anchor_date).days
    weeks_delta = days_delta // 7
    return weeks_delta % interval == 0


def recurrence_rule_payload_for_event(
    event: discord.ScheduledEvent,
) -> Mapping[str, object] | None:
    """Best-effort recurrence payload from a typed event.

    discord.py does not expose ``recurrence_rule`` on ``ScheduledEvent``. Recurrence
    for list/hydrate paths comes from the raw HTTP adapter instead. This helper only
    accepts an explicit mapping if a caller/test attached one on the instance.
    """
    try:
        recurrence_value = event.__dict__.get("recurrence_rule")
    except AttributeError:
        return None
    if recurrence_value is None:
        return None
    if isinstance(recurrence_value, Mapping):
        return cast(Mapping[str, object], recurrence_value)
    return None


def occurrence_start_utc_for_local_date(
    *,
    event_start_utc: dt.datetime,
    target_local_date: dt.date,
    local_timezone: dt.tzinfo,
    recurrence_rule: Mapping[str, object] | None = None,
) -> dt.datetime | None:
    event_start_local = event_start_utc.astimezone(local_timezone)
    anchor_date = event_start_local.date()
    if recurrence_rule is None:
        if anchor_date != target_local_date:
            return None
        return event_start_utc

    if not is_weekly_frequency(recurrence_rule.get("frequency")):
        return None
    if not weekly_rule_matches_target_date(
        recurrence_rule=recurrence_rule,
        anchor_date=anchor_date,
        target_local_date=target_local_date,
    ):
        return None

    occurrence_local = dt.datetime.combine(
        target_local_date,
        event_start_local.timetz().replace(tzinfo=None),
        tzinfo=local_timezone,
    )
    return occurrence_local.astimezone(dt.UTC)
