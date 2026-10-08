from __future__ import annotations

import asyncio
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
from butler.jobs.runtime import (
    ButlerScheduler,
    set_runtime_scheduler,
)
from tests.discord_mocks import make_guild, make_interaction, make_scheduled_event


@pytest.fixture
async def scheduler() -> Any:
    bot = MagicMock()
    bot.guilds = []
    bot.get_guild = MagicMock(return_value=None)
    runtime = ButlerScheduler(get_bot=lambda: bot)
    set_runtime_scheduler(runtime)
    runtime.start()
    try:
        yield runtime
    finally:
        runtime.cancel_guild_retries()
        runtime.cancel_daily_event_cache_job()
        if runtime.running:
            runtime.shutdown(wait=False)
        set_runtime_scheduler(None)


@pytest.fixture(autouse=True)
async def _reset_event_cache(scheduler: ButlerScheduler) -> Any:
    _ = scheduler
    events_cache.reset_connected_event_cache()
    event_warmup.reset_warm_guild_concurrency_lock_for_tests()
    yield
    events_cache.reset_connected_event_cache()
    event_warmup.reset_warm_guild_concurrency_lock_for_tests()


async def test_warm_guilds_use_shared_guild_concurrency_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(event_warmup, "EVENT_CACHE_WARM_GUILD_CONCURRENCY", 3)
    event_warmup.reset_warm_guild_concurrency_lock_for_tests()

    in_flight = 0
    max_in_flight = 0
    gate = asyncio.Lock()

    async def _fake_load(*, guild: object) -> object:
        from butler.caches.events.listing import ReusableEventsLoad

        nonlocal in_flight, max_in_flight
        _ = guild
        async with gate:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.05)
        async with gate:
            in_flight -= 1
        return ReusableEventsLoad(events=[], http_ok=True)

    monkeypatch.setattr(
        event_warmup,
        "load_reusable_events_for_guild",
        _fake_load,
    )

    guilds = [make_guild(guild_id=i) for i in range(1, 7)]
    await event_warmup.warmup_connected_event_cache(guilds=guilds, force=True)

    assert max_in_flight >= 2
    assert max_in_flight <= 3
    for guild in guilds:
        assert event_option_cache_is_fresh(guild_id=guild.id) is True

    first = event_warmup.warm_guild_concurrency_lock()
    second = event_warmup.warm_guild_concurrency_lock()
    assert first is second


async def test_warm_guild_failure_leaves_cache_cold_and_isolates_siblings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failing = make_guild(guild_id=1)
    ok = make_guild(guild_id=2)

    async def _fake_load(*, guild: object) -> object:
        from butler.caches.events.listing import ReusableEventsLoad

        guild_any = cast(Any, guild)
        if guild_any.id == 1:
            raise RuntimeError("list exploded")
        return ReusableEventsLoad(events=[], http_ok=True)

    monkeypatch.setattr(
        event_warmup,
        "load_reusable_events_for_guild",
        _fake_load,
    )

    await event_warmup.warmup_connected_event_cache(guilds=[failing, ok], force=True)

    assert event_option_cache_is_fresh(guild_id=1) is False
    assert 1 not in AUTOCOMPLETE_EVENT_CACHE
    assert 1 not in AUTOCOMPLETE_EVENT_CACHE_AT
    assert event_option_cache_is_fresh(guild_id=2) is True
    assert AUTOCOMPLETE_EVENT_CACHE.get(2) == []
    assert events_cache.event_cache_retry_pending(guild_id=1) is True
    assert events_cache.event_cache_retry_pending(guild_id=2) is False
    events_cache.cancel_pending_event_cache_retries()


async def test_http_failure_keeps_prior_options_and_schedules_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from butler.caches.events.listing import ReusableEventsLoad
    from butler.caches.events.option_cache import cache_reusable_events_for_guild

    guild = make_guild(guild_id=55)
    prior = make_scheduled_event(event_id=9, name="Prior")
    cache_reusable_events_for_guild(guild_id=55, events=[prior])
    assert event_option_cache_is_fresh(guild_id=55) is True

    async def _http_down(*, guild: object) -> ReusableEventsLoad:
        _ = guild
        return ReusableEventsLoad(events=[], http_ok=False)

    monkeypatch.setattr(event_warmup, "load_reusable_events_for_guild", _http_down)

    result = await event_warmup.warmup_connected_event_cache(guilds=[guild], force=True)

    assert result.failed == 1
    # Prior options remain (not wiped by a fake empty success).
    assert AUTOCOMPLETE_EVENT_CACHE.get(55)
    assert event_option_cache_is_fresh(guild_id=55) is True
    assert events_cache.event_cache_retry_pending(guild_id=55) is True
    events_cache.cancel_pending_event_cache_retries()


async def test_transient_network_error_triggers_single_reschedule_then_recovers(
    monkeypatch: pytest.MonkeyPatch,
    scheduler: ButlerScheduler,
) -> None:
    """TimeoutError on first list -> one date job; job warms successfully."""
    monkeypatch.setattr(
        "butler.jobs.runtime.EVENT_CACHE_RETRY_DELAY_SECONDS",
        0.05,
    )
    # Re-bind delay used inside schedule_guild_retry via constants import in runtime.
    import butler.jobs.runtime as jobs_runtime

    monkeypatch.setattr(jobs_runtime, "EVENT_CACHE_RETRY_DELAY_SECONDS", 0.05)

    guild = make_guild(guild_id=99)
    bot = MagicMock()
    bot.guilds = [guild]
    bot.get_guild = MagicMock(side_effect=lambda gid: guild if gid == 99 else None)
    scheduler._get_bot = lambda: bot

    list_attempts = 0
    reschedule_count = 0
    real_schedule = event_warmup.schedule_event_cache_retry

    def _count_successful_reschedule(*, guild: object, attempt: int = 1) -> bool:
        nonlocal reschedule_count
        scheduled = real_schedule(guild=cast(Any, guild), attempt=attempt)
        if scheduled:
            reschedule_count += 1
        return scheduled

    async def _flaky_network(*, guild: object) -> object:
        from butler.caches.events.listing import ReusableEventsLoad

        nonlocal list_attempts
        _ = guild
        list_attempts += 1
        if list_attempts == 1:
            raise TimeoutError("temporary connect timeout")
        return ReusableEventsLoad(events=[], http_ok=True)

    monkeypatch.setattr(
        event_warmup,
        "load_reusable_events_for_guild",
        _flaky_network,
    )
    monkeypatch.setattr(
        event_warmup,
        "schedule_event_cache_retry",
        _count_successful_reschedule,
    )

    await event_warmup.warmup_connected_event_cache(guilds=[guild], force=True)

    assert list_attempts == 1
    assert event_option_cache_is_fresh(guild_id=99) is False
    assert 99 not in AUTOCOMPLETE_EVENT_CACHE
    assert events_cache.event_cache_retry_pending(guild_id=99) is True
    assert reschedule_count == 1

    assert event_warmup.schedule_event_cache_retry(guild=guild) is False
    assert reschedule_count == 1

    job = scheduler.apscheduler.get_job(jobs_runtime.guild_retry_job_id(99))
    assert job is not None
    # Execute the scheduled job body (date trigger may not fire under test timing).
    await scheduler._run_guild_retry_warm(99)
    job.remove()

    assert list_attempts == 2
    assert event_option_cache_is_fresh(guild_id=99) is True
    assert AUTOCOMPLETE_EVENT_CACHE.get(99) == []
    assert events_cache.event_cache_retry_pending(guild_id=99) is False
    assert reschedule_count == 1


async def test_cold_autocomplete_schedules_retry_and_degrades(
    monkeypatch: pytest.MonkeyPatch,
    scheduler: ButlerScheduler,
) -> None:
    warm = AsyncMock()
    monkeypatch.setattr(event_warmup, "warmup_connected_event_cache", warm)
    monkeypatch.setattr(
        "butler.jobs.runtime.EVENT_CACHE_RETRY_DELAY_SECONDS",
        60.0,
    )
    import butler.jobs.runtime as jobs_runtime

    monkeypatch.setattr(jobs_runtime, "EVENT_CACHE_RETRY_DELAY_SECONDS", 60.0)

    guild = make_guild(guild_id=42)
    bot = MagicMock()
    bot.get_guild = MagicMock(return_value=guild)
    scheduler._get_bot = lambda: bot

    ix = make_interaction(guild=guild)

    choices = await event_autocomplete.autocomplete_existing_or_create_event(
        ix.interaction,
        current="",
    )

    assert len(choices) == 1
    assert choices[0].value == CREATE_NEW_EVENT_CHOICE_VALUE
    assert events_cache.event_cache_retry_pending(guild_id=42) is True

    again = await event_autocomplete.autocomplete_existing_event(ix.interaction, "")
    assert again == []
    assert events_cache.event_cache_retry_pending(guild_id=42) is True

    # Still only one scheduled job (deduped).
    assert event_warmup.schedule_event_cache_retry(guild=guild) is False
    events_cache.cancel_pending_event_cache_retries()
    assert events_cache.event_cache_retry_pending(guild_id=42) is False
    warm.assert_not_awaited()


async def test_schedule_event_cache_retry_dedupes_pending_task(
    monkeypatch: pytest.MonkeyPatch,
    scheduler: ButlerScheduler,
) -> None:
    _ = scheduler
    monkeypatch.setattr(
        "butler.jobs.runtime.EVENT_CACHE_RETRY_DELAY_SECONDS",
        60.0,
    )
    import butler.jobs.runtime as jobs_runtime

    monkeypatch.setattr(jobs_runtime, "EVENT_CACHE_RETRY_DELAY_SECONDS", 60.0)

    guild = cast(Any, MagicMock())
    guild.id = 7

    assert event_warmup.schedule_event_cache_retry(guild=guild) is True
    assert event_warmup.schedule_event_cache_retry(guild=guild) is False
    assert events_cache.event_cache_retry_pending(guild_id=7) is True

    events_cache.cancel_pending_event_cache_retries()
    assert events_cache.event_cache_retry_pending(guild_id=7) is False
