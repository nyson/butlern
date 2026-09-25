from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from butler.caches import events as events_cache
from butler.caches.events import autocomplete as event_autocomplete
from butler.caches.events import warmup as event_warmup
from butler.caches.events.option_cache import (
    AUTOCOMPLETE_EVENT_CACHE,
    AUTOCOMPLETE_EVENT_CACHE_AT,
    event_option_cache_is_fresh,
)
from butler.design import CREATE_NEW_EVENT_CHOICE_VALUE
from tests.discord_mocks import make_guild, make_interaction


@pytest.fixture(autouse=True)
def _reset_event_cache() -> Iterator[None]:
    events_cache.reset_connected_event_cache()
    yield
    events_cache.reset_connected_event_cache()


async def test_warm_guild_failure_leaves_cache_cold_and_isolates_siblings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failing = make_guild(guild_id=1)
    ok = make_guild(guild_id=2)

    async def _fake_reusable(*, guild: object) -> list[object]:
        guild_any = cast(Any, guild)
        if guild_any.id == 1:
            raise RuntimeError("list exploded")
        return []

    monkeypatch.setattr(
        event_warmup,
        "reusable_scheduled_events_for_guild",
        _fake_reusable,
    )

    await event_warmup.warmup_connected_event_cache(guilds=[failing, ok], force=True)

    assert event_option_cache_is_fresh(guild_id=1) is False
    assert 1 not in AUTOCOMPLETE_EVENT_CACHE
    assert 1 not in AUTOCOMPLETE_EVENT_CACHE_AT
    assert event_option_cache_is_fresh(guild_id=2) is True
    assert AUTOCOMPLETE_EVENT_CACHE.get(2) == []
    # Failed guild gets a delayed retry without waiting for autocomplete.
    assert events_cache.event_cache_retry_pending(guild_id=1) is True
    assert events_cache.event_cache_retry_pending(guild_id=2) is False
    events_cache.cancel_pending_event_cache_retries()


async def test_cold_autocomplete_schedules_retry_and_degrades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(event_warmup, "EVENT_CACHE_RETRY_DELAY_SECONDS", 0.01)
    warm = AsyncMock()
    monkeypatch.setattr(event_warmup, "warmup_connected_event_cache", warm)

    guild = make_guild(guild_id=42)
    ix = make_interaction(guild=guild)

    choices = await event_autocomplete.autocomplete_existing_or_create_event(
        ix.interaction,
        current="",
    )

    assert len(choices) == 1
    assert choices[0].value == CREATE_NEW_EVENT_CHOICE_VALUE
    assert events_cache.event_cache_retry_pending(guild_id=42) is True

    # Second cold lookup must not stack another task.
    again = await event_autocomplete.autocomplete_existing_event(ix.interaction, "")
    assert again == []
    assert events_cache.event_cache_retry_pending(guild_id=42) is True

    task = event_warmup._PENDING_RETRIES[42]
    await task
    warm.assert_awaited_once()
    assert events_cache.event_cache_retry_pending(guild_id=42) is False


async def test_schedule_event_cache_retry_dedupes_pending_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(event_warmup, "EVENT_CACHE_RETRY_DELAY_SECONDS", 60.0)
    warm = AsyncMock()
    monkeypatch.setattr(event_warmup, "warmup_connected_event_cache", warm)

    guild = cast(Any, MagicMock())
    guild.id = 7

    assert event_warmup.schedule_event_cache_retry(guild=guild) is True
    assert event_warmup.schedule_event_cache_retry(guild=guild) is False
    assert events_cache.event_cache_retry_pending(guild_id=7) is True

    events_cache.cancel_pending_event_cache_retries()
    assert events_cache.event_cache_retry_pending(guild_id=7) is False
    warm.assert_not_awaited()
