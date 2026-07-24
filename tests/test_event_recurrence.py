from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from butler.rsvp.event_command import (
    occurrence_start_utc_for_local_date,
)

STOCKHOLM = ZoneInfo("Europe/Stockholm")


def test_occurrence_start_non_recurring_same_day_matches_anchor() -> None:
    event_start_utc = dt.datetime(2026, 7, 24, 17, 0, tzinfo=dt.UTC)
    target_date = dt.date(2026, 7, 24)

    result = occurrence_start_utc_for_local_date(
        event_start_utc=event_start_utc,
        target_local_date=target_date,
        local_timezone=STOCKHOLM,
    )

    assert result == event_start_utc


def test_occurrence_start_non_recurring_different_day_does_not_match() -> None:
    event_start_utc = dt.datetime(2026, 7, 24, 17, 0, tzinfo=dt.UTC)
    target_date = dt.date(2026, 7, 31)

    result = occurrence_start_utc_for_local_date(
        event_start_utc=event_start_utc,
        target_local_date=target_date,
        local_timezone=STOCKHOLM,
    )

    assert result is None


def test_occurrence_start_weekly_recurrence_matches_next_weekday() -> None:
    event_start_utc = dt.datetime(2026, 7, 24, 17, 0, tzinfo=dt.UTC)  # Friday 19:00 local
    target_date = dt.date(2026, 7, 31)  # Next Friday

    result = occurrence_start_utc_for_local_date(
        event_start_utc=event_start_utc,
        target_local_date=target_date,
        local_timezone=STOCKHOLM,
        recurrence_rule={"frequency": "weekly", "interval": 1, "by_weekday": [4]},
    )

    assert result == dt.datetime(2026, 7, 31, 17, 0, tzinfo=dt.UTC)


def test_occurrence_start_weekly_interval_two_matches_every_other_week() -> None:
    event_start_utc = dt.datetime(2026, 7, 24, 17, 0, tzinfo=dt.UTC)

    one_week_ahead = occurrence_start_utc_for_local_date(
        event_start_utc=event_start_utc,
        target_local_date=dt.date(2026, 7, 31),
        local_timezone=STOCKHOLM,
        recurrence_rule={"frequency": "weekly", "interval": 2, "by_weekday": [4]},
    )
    two_weeks_ahead = occurrence_start_utc_for_local_date(
        event_start_utc=event_start_utc,
        target_local_date=dt.date(2026, 8, 7),
        local_timezone=STOCKHOLM,
        recurrence_rule={"frequency": "weekly", "interval": 2, "by_weekday": [4]},
    )

    assert one_week_ahead is None
    assert two_weeks_ahead == dt.datetime(2026, 8, 7, 17, 0, tzinfo=dt.UTC)


def test_occurrence_start_weekly_defaults_weekday_to_anchor_when_omitted() -> None:
    event_start_utc = dt.datetime(2026, 7, 24, 17, 0, tzinfo=dt.UTC)

    friday_match = occurrence_start_utc_for_local_date(
        event_start_utc=event_start_utc,
        target_local_date=dt.date(2026, 7, 31),
        local_timezone=STOCKHOLM,
        recurrence_rule={"frequency": "weekly", "interval": 1},
    )
    saturday_no_match = occurrence_start_utc_for_local_date(
        event_start_utc=event_start_utc,
        target_local_date=dt.date(2026, 8, 1),
        local_timezone=STOCKHOLM,
        recurrence_rule={"frequency": "weekly", "interval": 1},
    )

    assert friday_match == dt.datetime(2026, 7, 31, 17, 0, tzinfo=dt.UTC)
    assert saturday_no_match is None


def test_occurrence_start_weekly_respects_end_date_bound() -> None:
    event_start_utc = dt.datetime(2026, 7, 24, 17, 0, tzinfo=dt.UTC)
    recurrence = {
        "frequency": "weekly",
        "interval": 1,
        "by_weekday": [4],
        "end": "2026-07-31T23:00:00+00:00",
    }

    on_end_week = occurrence_start_utc_for_local_date(
        event_start_utc=event_start_utc,
        target_local_date=dt.date(2026, 7, 31),
        local_timezone=STOCKHOLM,
        recurrence_rule=recurrence,
    )
    after_end_week = occurrence_start_utc_for_local_date(
        event_start_utc=event_start_utc,
        target_local_date=dt.date(2026, 8, 7),
        local_timezone=STOCKHOLM,
        recurrence_rule=recurrence,
    )

    assert on_end_week == dt.datetime(2026, 7, 31, 17, 0, tzinfo=dt.UTC)
    assert after_end_week is None


