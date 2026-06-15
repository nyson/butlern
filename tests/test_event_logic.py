"""Unit tests for the pure event-input core (`butler.event_logic`).

These need no Discord objects — `now_local` is injected so time logic is deterministic.
"""

from __future__ import annotations

import datetime as dt

import pytest

from butler.event_logic import (
    EventInput,
    normalize_room_url,
    parse_time_today,
    require_non_empty,
    resolve_event_input,
)

# A fixed "now" in a non-UTC zone so we can assert UTC conversion is real.
TZ = dt.timezone(dt.timedelta(hours=2))
NOON = dt.datetime(2026, 6, 15, 12, 0, tzinfo=TZ)
EVENING = dt.datetime(2026, 6, 15, 20, 0, tzinfo=TZ)
DEFAULT_START = "19:00"
DEFAULT_DURATION = dt.timedelta(hours=4)


# --- parse_time_today -------------------------------------------------------


def test_parse_time_today_valid() -> None:
    result = parse_time_today("18:30", NOON)
    assert result == dt.datetime(2026, 6, 15, 18, 30, tzinfo=TZ)


def test_parse_time_today_carries_tzinfo() -> None:
    result = parse_time_today("18:30", NOON)
    assert result is not None
    assert result.tzinfo == TZ


@pytest.mark.parametrize("bad", ["nope", "25:00", "18:60", "", "1830", "18.30"])
def test_parse_time_today_invalid_returns_none(bad: str) -> None:
    assert parse_time_today(bad, NOON) is None


# --- normalize_room_url -----------------------------------------------------


def test_normalize_room_url_none() -> None:
    assert normalize_room_url(None) is None


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_normalize_room_url_blank_is_none(blank: str) -> None:
    assert normalize_room_url(blank) is None


@pytest.mark.parametrize(
    "url",
    ["http://example.com", "https://example.com/room", "https://sub.example.com/a?b=c"],
)
def test_normalize_room_url_valid_passthrough(url: str) -> None:
    assert normalize_room_url(url) == url


def test_normalize_room_url_strips_whitespace() -> None:
    assert normalize_room_url("  https://example.com/room  ") == "https://example.com/room"


@pytest.mark.parametrize(
    "bad",
    ["ftp://example.com", "example.com", "notaurl", "http://", "https://"],
)
def test_normalize_room_url_invalid_raises(bad: str) -> None:
    with pytest.raises(ValueError, match="must be a full URL"):
        normalize_room_url(bad)


# --- require_non_empty ------------------------------------------------------


def test_require_non_empty_trims() -> None:
    assert require_non_empty("  hello  ", "title") == "hello"


@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
def test_require_non_empty_blank_raises_naming_field(blank: str) -> None:
    with pytest.raises(ValueError, match="`title` is required"):
        require_non_empty(blank, "title")


# --- resolve_event_input ----------------------------------------------------


def test_resolve_event_input_explicit_future_time() -> None:
    result = resolve_event_input(
        title="  Game Night  ",
        description="  Bring snacks  ",
        room_link="https://example.com/room",
        start_time="20:00",
        now_local=NOON,
        default_start_time=DEFAULT_START,
        default_duration=DEFAULT_DURATION,
    )
    assert isinstance(result, EventInput)
    # Strings are trimmed.
    assert result.title == "Game Night"
    assert result.description == "Bring snacks"
    assert result.room_url == "https://example.com/room"
    # 20:00 +02:00 local.
    assert result.start_local == dt.datetime(2026, 6, 15, 20, 0, tzinfo=TZ)
    # UTC conversion: 20:00 +02:00 -> 18:00 UTC.
    assert result.start_utc == dt.datetime(2026, 6, 15, 18, 0, tzinfo=dt.UTC)
    # end = start + duration.
    assert result.end_utc == result.start_utc + DEFAULT_DURATION


def test_resolve_event_input_default_rolls_to_tomorrow_when_passed() -> None:
    # Default 19:00 has already passed at 20:00 -> roll to tomorrow.
    result = resolve_event_input(
        title="t",
        description="d",
        room_link=None,
        start_time=None,
        now_local=EVENING,
        default_start_time=DEFAULT_START,
        default_duration=DEFAULT_DURATION,
    )
    assert result.start_local == dt.datetime(2026, 6, 16, 19, 0, tzinfo=TZ)
    assert result.start_utc == dt.datetime(2026, 6, 16, 17, 0, tzinfo=dt.UTC)


@pytest.mark.parametrize("blank_start", [None, "", "   "])
def test_resolve_event_input_blank_start_uses_default(blank_start: str | None) -> None:
    # At noon the default 19:00 is still in the future -> same day, no rollover.
    result = resolve_event_input(
        title="t",
        description="d",
        room_link=None,
        start_time=blank_start,
        now_local=NOON,
        default_start_time=DEFAULT_START,
        default_duration=DEFAULT_DURATION,
    )
    assert result.start_local == dt.datetime(2026, 6, 15, 19, 0, tzinfo=TZ)


def test_resolve_event_input_explicit_past_time_raises() -> None:
    with pytest.raises(ValueError, match="already passed today"):
        resolve_event_input(
            title="t",
            description="d",
            room_link=None,
            start_time="08:00",
            now_local=NOON,
            default_start_time=DEFAULT_START,
            default_duration=DEFAULT_DURATION,
        )


def test_resolve_event_input_invalid_time_format_raises() -> None:
    with pytest.raises(ValueError, match="Invalid time format"):
        resolve_event_input(
            title="t",
            description="d",
            room_link=None,
            start_time="quarter past",
            now_local=NOON,
            default_start_time=DEFAULT_START,
            default_duration=DEFAULT_DURATION,
        )


def test_resolve_event_input_blank_title_raises() -> None:
    with pytest.raises(ValueError, match="`title` is required"):
        resolve_event_input(
            title="   ",
            description="d",
            room_link=None,
            start_time="20:00",
            now_local=NOON,
            default_start_time=DEFAULT_START,
            default_duration=DEFAULT_DURATION,
        )


def test_resolve_event_input_invalid_room_link_surfaces_url_error() -> None:
    with pytest.raises(ValueError, match="must be a full URL"):
        resolve_event_input(
            title="t",
            description="d",
            room_link="not-a-url",
            start_time="20:00",
            now_local=NOON,
            default_start_time=DEFAULT_START,
            default_duration=DEFAULT_DURATION,
        )
