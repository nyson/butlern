from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from butler.jobs.runtime import (
    DAILY_EVENT_CACHE_JOB_ID,
    ButlerScheduler,
    set_runtime_scheduler,
)


@pytest.fixture
def bot() -> MagicMock:
    b = MagicMock()
    b.guilds = []
    b.get_guild = MagicMock(return_value=None)
    return b


async def test_daily_job_registered_after_start(bot: MagicMock) -> None:
    sched = ButlerScheduler(get_bot=lambda: bot)
    set_runtime_scheduler(sched)
    try:
        sched.start()
        sched.ensure_daily_event_cache_job()
        job = sched.apscheduler.get_job(DAILY_EVENT_CACHE_JOB_ID)
        assert job is not None
        assert sched.running is True
    finally:
        sched.cancel_daily_event_cache_job()
        sched.shutdown(wait=False)
        set_runtime_scheduler(None)


async def test_guild_retry_dedupes_and_runs(
    bot: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import butler.jobs.runtime as jobs_runtime

    monkeypatch.setattr(jobs_runtime, "EVENT_CACHE_RETRY_DELAY_SECONDS", 0.05)
    guild = MagicMock()
    guild.id = 5
    bot.get_guild = MagicMock(return_value=guild)
    bot.guilds = [guild]

    warm = AsyncMock(return_value=MagicMock(warmed=0, empty=1, failed=0, elapsed_ms=1.0))
    monkeypatch.setattr(jobs_runtime, "warmup_connected_event_cache", warm)

    sched = ButlerScheduler(get_bot=lambda: bot)
    set_runtime_scheduler(sched)
    try:
        sched.start()
        assert sched.schedule_guild_retry(guild_id=5) is True
        assert sched.schedule_guild_retry(guild_id=5) is False
        assert sched.guild_retry_pending(guild_id=5) is True

        job = sched.apscheduler.get_job(jobs_runtime.guild_retry_job_id(5))
        assert job is not None
        await sched._run_guild_retry_warm(5)
        job.remove()

        warm.assert_awaited()
        assert sched.guild_retry_pending(guild_id=5) is False
    finally:
        sched.cancel_guild_retries()
        sched.shutdown(wait=False)
        set_runtime_scheduler(None)
