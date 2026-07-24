from __future__ import annotations

from collections.abc import Mapping
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from butler.rsvp.recurrence_adapter import (
    fetch_recurrence_rules_for_guild,
    normalize_recurrence_rule_payload,
    recurrence_rules_from_raw_scheduled_events,
)


def test_normalize_recurrence_rule_payload_weekly() -> None:
    raw_rule: Mapping[str, object] = {
        "frequency": 2,
        "interval": 1,
        "by_weekday": [4],
        "end": "2026-07-31T23:00:00+00:00",
    }

    normalized = normalize_recurrence_rule_payload(raw_rule)

    assert normalized == {
        "frequency": 2,
        "interval": 1,
        "by_weekday": [4],
        "end": "2026-07-31T23:00:00+00:00",
    }


@pytest.mark.parametrize(
    "raw_rule",
    [
        {"interval": 1, "by_weekday": [4]},
        {"frequency": "weekly", "interval": 0},
        {"frequency": "weekly", "by_weekday": ["fri"]},
        {"frequency": "weekly", "by_weekday": [7]},
    ],
)
def test_normalize_recurrence_rule_payload_invalid_cases_return_none(
    raw_rule: Mapping[str, object],
) -> None:
    assert normalize_recurrence_rule_payload(raw_rule) is None


def test_recurrence_rules_from_raw_scheduled_events_extracts_only_valid_rules() -> None:
    raw_events: list[Mapping[str, object]] = [
        {
            "id": "100",
            "recurrence_rule": {"frequency": 2, "interval": 1, "by_weekday": [4]},
        },
        {
            "id": "101",
            "recurrence_rule": {"interval": 1},
        },
        {
            "id": "not-an-int",
            "recurrence_rule": {"frequency": 2},
        },
        {
            "id": "102",
        },
    ]

    result = recurrence_rules_from_raw_scheduled_events(raw_events)

    assert result == {
        100: {"frequency": 2, "interval": 1, "by_weekday": [4]},
    }


async def test_fetch_recurrence_rules_for_guild_reads_http_payload() -> None:
    guild = MagicMock(spec=discord.Guild)
    guild.id = 123
    guild._state = MagicMock()  # pyright: ignore[reportAttributeAccessIssue]
    guild._state.http = MagicMock()  # pyright: ignore[reportAttributeAccessIssue]
    guild._state.http.get_scheduled_events = AsyncMock(  # pyright: ignore[reportAttributeAccessIssue]
        return_value=[
            {
                "id": "555",
                "recurrence_rule": {
                    "frequency": "weekly",
                    "interval": 2,
                    "by_weekday": [4],
                },
            }
        ]
    )

    result = await fetch_recurrence_rules_for_guild(guild=cast(discord.Guild, guild))

    assert result == {
        555: {
            "frequency": "weekly",
            "interval": 2,
            "by_weekday": [4],
        }
    }


async def test_fetch_recurrence_rules_for_guild_handles_http_errors() -> None:
    guild = MagicMock(spec=discord.Guild)
    guild.id = 123
    guild._state = MagicMock()  # pyright: ignore[reportAttributeAccessIssue]
    guild._state.http = MagicMock()  # pyright: ignore[reportAttributeAccessIssue]
    response = MagicMock()
    response.status = 500
    response.reason = "Server Error"
    guild._state.http.get_scheduled_events = AsyncMock(  # pyright: ignore[reportAttributeAccessIssue]
        side_effect=discord.HTTPException(response, "boom")
    )

    result = await fetch_recurrence_rules_for_guild(guild=cast(discord.Guild, guild))

    assert result == {}
