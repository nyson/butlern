"""Process-local APScheduler runtime for event-cache jobs.

Acceptance goals:
- option cache does not stay stale: periodic force warm + cold invalidation on failure
- transient failures schedule a future one-shot job within EVENT_CACHE_RETRY_DELAY_SECONDS
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from apscheduler.schedulers.base import STATE_RUNNING  # type: ignore[import-untyped]
from discord.ext import commands

from butler.caches.events.constants import (
    EVENT_CACHE_RETRY_BACKOFF_FACTOR,
    EVENT_CACHE_RETRY_DELAY_SECONDS,
    EVENT_CACHE_RETRY_MAX_ATTEMPTS,
    EVENT_CACHE_RETRY_MAX_DELAY_SECONDS,
    EVENT_OPTION_CACHE_TTL_SECONDS,
)
from butler.caches.events.warmup import EventCacheWarmResult, warmup_connected_event_cache

logger = logging.getLogger(__name__)

BotFactory = Callable[[], commands.Bot]

DAILY_EVENT_CACHE_JOB_ID = "event-cache-daily-warm"
GUILD_RETRY_JOB_ID_PREFIX = "event-cache-retry-"

# Interval slightly under soft TTL so force warm runs before options expire.
EVENT_CACHE_DAILY_INTERVAL_SECONDS = max(60.0, EVENT_OPTION_CACHE_TTL_SECONDS * 0.9)


def guild_retry_job_id(guild_id: int) -> str:
    return f"{GUILD_RETRY_JOB_ID_PREFIX}{guild_id}"


def retry_delay_seconds(*, attempt: int) -> float:
    """Exponential backoff delay for retry attempt (1-based)."""
    safe_attempt = max(1, attempt)
    delay = EVENT_CACHE_RETRY_DELAY_SECONDS * (
        EVENT_CACHE_RETRY_BACKOFF_FACTOR ** (safe_attempt - 1)
    )
    return float(min(delay, EVENT_CACHE_RETRY_MAX_DELAY_SECONDS))


class ButlerScheduler:
    """Thin facade over ``AsyncIOScheduler`` for Butler background work."""

    def __init__(self, *, get_bot: BotFactory) -> None:
        self._get_bot = get_bot
        self._scheduler: AsyncIOScheduler | None = None
        self._bound_loop: asyncio.AbstractEventLoop | None = None
        self._daily_registered = False
        self._grace = max(1, int(EVENT_CACHE_RETRY_DELAY_SECONDS * 5))

    def _build_scheduler(self, loop: asyncio.AbstractEventLoop) -> AsyncIOScheduler:
        return AsyncIOScheduler(
            event_loop=loop,
            timezone=UTC,
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": self._grace,
            },
        )

    def _ensure_scheduler(self) -> AsyncIOScheduler:
        loop = asyncio.get_running_loop()
        if self._scheduler is not None and self._bound_loop is loop and not loop.is_closed():
            return self._scheduler

        if self._scheduler is not None:
            try:
                if self._scheduler.state == STATE_RUNNING:
                    self._scheduler.shutdown(wait=False)
            except Exception:
                logger.debug("Prior APScheduler shutdown failed", exc_info=True)

        self._scheduler = self._build_scheduler(loop)
        self._bound_loop = loop
        self._daily_registered = False
        return self._scheduler

    @property
    def apscheduler(self) -> AsyncIOScheduler:
        return self._ensure_scheduler()

    @property
    def running(self) -> bool:
        if self._scheduler is None:
            return False
        return bool(self._scheduler.state == STATE_RUNNING)

    def start(self) -> None:
        scheduler = self._ensure_scheduler()
        if scheduler.state == STATE_RUNNING:
            return
        scheduler.start(paused=False)
        logger.debug("APScheduler started.")

    def shutdown(self, *, wait: bool = False) -> None:
        if self._scheduler is None or self._scheduler.state != STATE_RUNNING:
            return
        try:
            self._scheduler.shutdown(wait=wait)
        except RuntimeError:
            logger.debug("APScheduler shutdown skipped (no event loop).", exc_info=True)
        logger.debug("APScheduler shut down.")

    def ensure_daily_event_cache_job(self) -> None:
        """Register interval force-warm; first fire after one interval (boot covers now)."""
        scheduler = self._ensure_scheduler()
        if self._daily_registered and scheduler.get_job(DAILY_EVENT_CACHE_JOB_ID) is not None:
            return
        first = datetime.now(UTC) + timedelta(seconds=EVENT_CACHE_DAILY_INTERVAL_SECONDS)
        scheduler.add_job(
            self._run_daily_event_cache_warm,
            trigger="interval",
            seconds=EVENT_CACHE_DAILY_INTERVAL_SECONDS,
            id=DAILY_EVENT_CACHE_JOB_ID,
            name="daily event-cache force warm",
            replace_existing=True,
            next_run_time=first,
        )
        self._daily_registered = True
        logger.debug(
            "Registered daily event-cache job id=%s interval=%.0fs first=%s",
            DAILY_EVENT_CACHE_JOB_ID,
            EVENT_CACHE_DAILY_INTERVAL_SECONDS,
            first.isoformat(),
        )

    def schedule_guild_retry(self, *, guild_id: int, attempt: int = 1) -> bool:
        """Schedule a one-shot force warm for ``guild_id``. Dedupes while pending."""
        if attempt > EVENT_CACHE_RETRY_MAX_ATTEMPTS:
            logger.error(
                "Event-cache retry exhausted guild=%s attempts=%s; waiting for daily warm",
                guild_id,
                attempt - 1,
            )
            return False
        scheduler = self._ensure_scheduler()
        if scheduler.state != STATE_RUNNING:
            self.start()
        job_id = guild_retry_job_id(guild_id)
        if scheduler.get_job(job_id) is not None:
            return False
        delay = retry_delay_seconds(attempt=attempt)
        run_at = datetime.now(UTC) + timedelta(seconds=delay)
        scheduler.add_job(
            self._run_guild_retry_warm,
            trigger="date",
            run_date=run_at,
            id=job_id,
            name=f"event-cache retry guild={guild_id} attempt={attempt}",
            kwargs={"guild_id": guild_id, "attempt": attempt},
            replace_existing=True,
        )
        logger.info(
            "Scheduled event-cache retry job guild=%s attempt=%s at %s (in %.0fs)",
            guild_id,
            attempt,
            run_at.isoformat(),
            delay,
        )
        return True

    def guild_retry_pending(self, *, guild_id: int) -> bool:
        if self._scheduler is None:
            return False
        return self._scheduler.get_job(guild_retry_job_id(guild_id)) is not None

    def cancel_guild_retries(self) -> None:
        if self._scheduler is None:
            return
        prefix = GUILD_RETRY_JOB_ID_PREFIX
        for job in list(self._scheduler.get_jobs()):
            if job.id.startswith(prefix):
                job.remove()

    def cancel_daily_event_cache_job(self) -> None:
        if self._scheduler is None:
            return
        job = self._scheduler.get_job(DAILY_EVENT_CACHE_JOB_ID)
        if job is not None:
            job.remove()
        self._daily_registered = False

    async def warm_connected_guilds(
        self,
        *,
        force: bool = True,
        guild_ids: Sequence[int] | None = None,
        retry_attempt: int | None = None,
    ) -> EventCacheWarmResult:
        bot = self._get_bot()
        if guild_ids is None:
            guilds = list(bot.guilds)
        else:
            guilds = []
            for guild_id in guild_ids:
                guild = bot.get_guild(guild_id)
                if guild is not None:
                    guilds.append(guild)
                else:
                    logger.warning(
                        "event-cache warm skipped missing guild_id=%s",
                        guild_id,
                    )
        return await warmup_connected_event_cache(
            guilds=guilds,
            force=force,
            retry_attempt=retry_attempt,
        )

    async def _run_daily_event_cache_warm(self) -> None:
        logger.debug("Daily event-cache warm job starting.")
        try:
            result = await self.warm_connected_guilds(force=True)
            if result.failed > 0:
                logger.warning(
                    "Daily event-cache warm finished with failures "
                    "warmed=%s empty=%s failed=%s took=%.1fms",
                    result.warmed,
                    result.empty,
                    result.failed,
                    result.elapsed_ms,
                )
            else:
                logger.debug(
                    "Daily event-cache warm finished warmed=%s empty=%s failed=%s "
                    "took=%.1fms",
                    result.warmed,
                    result.empty,
                    result.failed,
                    result.elapsed_ms,
                )
        except Exception:
            logger.exception("Daily event-cache warm job failed.")

    async def _run_guild_retry_warm(self, guild_id: int, attempt: int = 1) -> None:
        logger.info(
            "Running scheduled event-cache retry warm guild=%s attempt=%s",
            guild_id,
            attempt,
        )
        try:
            result = await self.warm_connected_guilds(
                force=True,
                guild_ids=[guild_id],
                retry_attempt=attempt,
            )
        except Exception:
            logger.exception(
                "Scheduled event-cache retry crashed guild=%s attempt=%s",
                guild_id,
                attempt,
            )
            self.schedule_guild_retry(guild_id=guild_id, attempt=attempt + 1)
            return

        if result.failed > 0 and not self.guild_retry_pending(guild_id=guild_id):
            # Warm should have scheduled next attempt; belt-and-suspenders.
            self.schedule_guild_retry(guild_id=guild_id, attempt=attempt + 1)


_RUNTIME: ButlerScheduler | None = None


def get_runtime_scheduler() -> ButlerScheduler | None:
    return _RUNTIME


def set_runtime_scheduler(scheduler: ButlerScheduler | None) -> None:
    global _RUNTIME
    _RUNTIME = scheduler


def schedule_event_cache_guild_retry(*, guild_id: int, attempt: int = 1) -> bool:
    """Module entry used by warmup failure paths."""
    runtime = _RUNTIME
    if runtime is None:
        logger.warning(
            "Cannot schedule event-cache retry guild=%s: scheduler not configured",
            guild_id,
        )
        return False
    return runtime.schedule_guild_retry(guild_id=guild_id, attempt=attempt)


def event_cache_guild_retry_pending(*, guild_id: int) -> bool:
    runtime = _RUNTIME
    if runtime is None:
        return False
    return runtime.guild_retry_pending(guild_id=guild_id)


def cancel_pending_event_cache_guild_retries() -> None:
    runtime = _RUNTIME
    if runtime is None:
        return
    runtime.cancel_guild_retries()


class DailyEventCacheJobHandle:
    """Test-facing handle compatible with former IntervalJob checks."""

    def __init__(self, scheduler: ButlerScheduler) -> None:
        self._scheduler = scheduler

    def start(self) -> None:
        self._scheduler.start()
        self._scheduler.ensure_daily_event_cache_job()

    def is_running(self) -> bool:
        if not self._scheduler.running:
            return False
        return self._scheduler.apscheduler.get_job(DAILY_EVENT_CACHE_JOB_ID) is not None

    def cancel(self) -> None:
        self._scheduler.cancel_daily_event_cache_job()

    def stop(self) -> None:
        self.cancel()
