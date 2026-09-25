from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from butler.caches.events import listing as event_listing
from tests.discord_mocks import make_guild, make_scheduled_event


async def test_list_scheduled_events_once_falls_back_on_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = make_scheduled_event(event_id=7, name="Fallback")
    guild = make_guild(guild_id=1, scheduled_events=[event])

    async def _boom(_guild: object) -> list[object]:
        raise OSError(101, "Connect call failed")

    monkeypatch.setattr(event_listing, "fetch_raw_scheduled_events", _boom)

    events, rules = await event_listing.list_scheduled_events_once(guild=guild)

    assert events == [event]
    assert rules == {}
    cast(Any, guild).fetch_scheduled_events.assert_awaited_once()


async def test_list_scheduled_events_once_returns_empty_when_both_paths_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guild = make_guild(guild_id=1, scheduled_events=[])
    cast(Any, guild).fetch_scheduled_events = AsyncMock(
        side_effect=TimeoutError("connection timed out")
    )

    async def _boom(_guild: object) -> list[object]:
        raise TimeoutError("raw http timed out")

    monkeypatch.setattr(event_listing, "fetch_raw_scheduled_events", _boom)

    events, rules = await event_listing.list_scheduled_events_once(guild=guild)

    assert events == []
    assert rules == {}


async def test_reusable_scheduled_events_uses_gateway_cache_after_list_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = make_scheduled_event(event_id=9, name="Gateway")
    guild = make_guild(guild_id=3, scheduled_events=[event])

    async def _boom(*, guild: object) -> tuple[list[object], dict[int, object]]:
        _ = guild
        raise RuntimeError("unexpected list failure")

    monkeypatch.setattr(event_listing, "list_scheduled_events_once", _boom)

    reusable = await event_listing.reusable_scheduled_events_for_guild(guild=guild)

    assert reusable == [event]


async def test_hydrate_skips_malformed_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guild = make_guild(guild_id=4)
    state = MagicMock()
    guild_any = cast(Any, guild)
    guild_any._state = state

    good = make_scheduled_event(event_id=1, name="Good")
    raw_events: list[Mapping[str, object]] = [
        {"id": "1", "name": "Good"},
        {"id": "2", "name": "Bad"},
    ]

    def _ctor(*, state: object, data: Mapping[str, object]) -> discord.ScheduledEvent:
        _ = state
        if data.get("id") == "2":
            raise KeyError("entity_metadata")
        return good

    monkeypatch.setattr(discord, "ScheduledEvent", _ctor)
    monkeypatch.setattr(
        event_listing,
        "fetch_raw_scheduled_events",
        AsyncMock(return_value=raw_events),
    )

    events, rules = await event_listing.list_scheduled_events_once(guild=guild)

    assert events == [good]
    assert rules == {}
    cast(Any, guild).fetch_scheduled_events.assert_not_awaited()


async def test_fetch_scheduled_event_by_id_rescues_oserror() -> None:
    guild = make_guild(guild_id=5)
    cast(Any, guild).get_scheduled_event = MagicMock(return_value=None)
    cast(Any, guild).fetch_scheduled_event = AsyncMock(
        side_effect=OSError(101, "Connect call failed")
    )

    result = await event_listing.fetch_scheduled_event_by_id(guild=guild, event_id=42)

    assert result is None
