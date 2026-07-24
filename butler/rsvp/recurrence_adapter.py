from __future__ import annotations

import inspect
from collections.abc import Mapping, Sequence
from typing import TypedDict, cast

import discord
from discord import HTTPException


class RecurrenceRulePayload(TypedDict, total=False):
    frequency: int | str
    interval: int
    by_weekday: list[int]
    end: str


def _normalize_frequency(value: object) -> int | str | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None


def _normalize_interval(value: object) -> int | None:
    if not isinstance(value, int):
        return None
    if value < 1:
        return None
    return value


def _normalize_by_weekday(value: object) -> list[int] | None:
    if not isinstance(value, list):
        return None
    normalized: list[int] = []
    for weekday in value:
        if not isinstance(weekday, int):
            return None
        if weekday < 0 or weekday > 6:
            return None
        normalized.append(weekday)
    return normalized


def normalize_recurrence_rule_payload(
    raw_rule: Mapping[str, object],
) -> RecurrenceRulePayload | None:
    frequency = _normalize_frequency(raw_rule.get("frequency"))
    if frequency is None:
        return None

    normalized: RecurrenceRulePayload = {"frequency": frequency}

    if "interval" in raw_rule:
        interval = _normalize_interval(raw_rule.get("interval"))
        if interval is None:
            return None
        normalized["interval"] = interval

    if "by_weekday" in raw_rule:
        by_weekday = _normalize_by_weekday(raw_rule.get("by_weekday"))
        if by_weekday is None:
            return None
        normalized["by_weekday"] = by_weekday

    end_value = raw_rule.get("end")
    if isinstance(end_value, str):
        normalized["end"] = end_value

    return normalized


def recurrence_rules_from_raw_scheduled_events(
    raw_events: Sequence[Mapping[str, object]],
) -> dict[int, RecurrenceRulePayload]:
    recurrence_by_event_id: dict[int, RecurrenceRulePayload] = {}
    for raw_event in raw_events:
        raw_event_id = raw_event.get("id")
        if not isinstance(raw_event_id, str):
            continue
        try:
            event_id = int(raw_event_id)
        except ValueError:
            continue

        raw_rule = raw_event.get("recurrence_rule")
        if not isinstance(raw_rule, Mapping):
            continue
        normalized_rule = normalize_recurrence_rule_payload(
            cast(Mapping[str, object], raw_rule),
        )
        if normalized_rule is None:
            continue
        recurrence_by_event_id[event_id] = normalized_rule
    return recurrence_by_event_id


async def _fetch_raw_scheduled_events(guild: discord.Guild) -> list[Mapping[str, object]]:
    state = getattr(guild, "_state", None)
    http = getattr(state, "http", None)
    if http is None:
        return []

    fetch_many = getattr(http, "get_scheduled_events", None)
    if not callable(fetch_many):
        return []

    raw_events_result = fetch_many(guild.id, with_user_count=False)
    if not inspect.isawaitable(raw_events_result):
        return []
    raw_events = await raw_events_result
    if not isinstance(raw_events, list):
        return []

    return [
        cast(Mapping[str, object], event)
        for event in raw_events
        if isinstance(event, Mapping)
    ]


async def fetch_recurrence_rules_for_guild(
    *,
    guild: discord.Guild,
) -> dict[int, RecurrenceRulePayload]:
    try:
        raw_events = await _fetch_raw_scheduled_events(guild)
    except (discord.Forbidden, HTTPException):
        return {}
    return recurrence_rules_from_raw_scheduled_events(raw_events)
