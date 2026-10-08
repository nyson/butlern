from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Literal

from discord import Guild

from butler.caches.events.constants import EVENT_CACHE_WARM_GUILD_CONCURRENCY
from butler.caches.events.listing import load_reusable_events_for_guild
from butler.caches.events.option_cache import (
    AUTOCOMPLETE_EVENT_CACHE,
    cache_reusable_events_for_guild,
    event_option_cache_is_fresh,
    invalidate_event_option_cache,
)
from butler.timing import stopwatch

logger = logging.getLogger(__name__)

# Per-guild mutual exclusion: force warm + retry + cold-path warm for one guild.
_WARMUP_LOCKS: dict[int, asyncio.Lock] = {}
# Process-wide cap on concurrent Guild warms (EVENT_CACHE_WARM_GUILD_CONCURRENCY).
_WARM_GUILD_CONCURRENCY_LOCK: asyncio.Semaphore | None = None

WarmOutcome = Literal["warmed", "empty", "skipped", "failed"]


@dataclass(frozen=True)
class EventCacheWarmResult:
    """Aggregate outcomes from a multi-guild (or single-guild) warm pass."""

    warmed: int = 0
    empty: int = 0
    skipped: int = 0
    failed: int = 0
    elapsed_ms: float = 0.0

    def with_warmed(self) -> EventCacheWarmResult:
        return EventCacheWarmResult(
            self.warmed + 1, self.empty, self.skipped, self.failed, self.elapsed_ms
        )

    def with_empty(self) -> EventCacheWarmResult:
        return EventCacheWarmResult(
            self.warmed, self.empty + 1, self.skipped, self.failed, self.elapsed_ms
        )

    def with_skipped(self) -> EventCacheWarmResult:
        return EventCacheWarmResult(
            self.warmed, self.empty, self.skipped + 1, self.failed, self.elapsed_ms
        )

    def with_failed(self) -> EventCacheWarmResult:
        return EventCacheWarmResult(
            self.warmed, self.empty, self.skipped, self.failed + 1, self.elapsed_ms
        )

    def with_outcome(self, outcome: WarmOutcome) -> EventCacheWarmResult:
        if outcome == "warmed":
            return self.with_warmed()
        if outcome == "empty":
            return self.with_empty()
        if outcome == "failed":
            return self.with_failed()
        return self.with_skipped()

    def with_elapsed_ms(self, elapsed_ms: float) -> EventCacheWarmResult:
        return EventCacheWarmResult(
            self.warmed, self.empty, self.skipped, self.failed, elapsed_ms
        )


def warmup_lock_for(guild_id: int) -> asyncio.Lock:
    """Per-guild lock so overlapping warms cannot double-stamp one guild."""
    lock = _WARMUP_LOCKS.get(guild_id)
    if lock is None:
        lock = asyncio.Lock()
        _WARMUP_LOCKS[guild_id] = lock
    return lock


def warm_guild_concurrency_lock() -> asyncio.Semaphore:
    """Shared semaphore bound to ``EVENT_CACHE_WARM_GUILD_CONCURRENCY``."""
    global _WARM_GUILD_CONCURRENCY_LOCK
    if _WARM_GUILD_CONCURRENCY_LOCK is None:
        _WARM_GUILD_CONCURRENCY_LOCK = asyncio.Semaphore(
            max(1, EVENT_CACHE_WARM_GUILD_CONCURRENCY)
        )
    return _WARM_GUILD_CONCURRENCY_LOCK


def reset_warm_guild_concurrency_lock_for_tests() -> None:
    """Drop the shared guild semaphore so tests can change concurrency and re-bind."""
    global _WARM_GUILD_CONCURRENCY_LOCK
    _WARM_GUILD_CONCURRENCY_LOCK = None


def cancel_pending_event_cache_retries() -> None:
    """Cancel scheduled guild retry jobs (tests / full cache reset)."""
    from butler.jobs import runtime as jobs_runtime

    jobs_runtime.cancel_pending_event_cache_guild_retries()


def event_cache_retry_pending(*, guild_id: int) -> bool:
    from butler.jobs import runtime as jobs_runtime

    return jobs_runtime.event_cache_guild_retry_pending(guild_id=guild_id)


def schedule_event_cache_retry(*, guild: Guild, attempt: int = 1) -> bool:
    """Schedule a force warm for ``guild`` after the retry delay via APScheduler.

    Dedupes per guild while a retry job is already pending. Returns True when a
    new retry was scheduled. ``attempt`` drives exponential backoff.
    """
    from butler.jobs import runtime as jobs_runtime

    return jobs_runtime.schedule_event_cache_guild_retry(
        guild_id=guild.id,
        attempt=attempt,
    )


def _should_skip_guild(*, guild_id: int, force: bool) -> bool:
    return not force and event_option_cache_is_fresh(guild_id=guild_id)


def _next_retry_attempt(*, retry_attempt: int | None) -> int:
    """Map in-flight retry attempt to the next schedule attempt (1-based)."""
    if retry_attempt is None:
        return 1
    return max(1, retry_attempt) + 1


async def _warm_guild(
    *,
    guild: Guild,
    force: bool,
    retry_attempt: int | None = None,
) -> WarmOutcome:
    """Warm one guild. Returns outcome: warmed | empty | skipped | failed."""
    if _should_skip_guild(guild_id=guild.id, force=force):
        return "skipped"

    async with warmup_lock_for(guild.id):
        if _should_skip_guild(guild_id=guild.id, force=force):
            return "skipped"

        # Keep any previous options readable until the new list is applied (or we
        # invalidate on hard failure). Autocomplete stays stale-while-revalidate
        # during slow Discord list HTTP (often ~10s) and network blips.
        try:
            loaded = await load_reusable_events_for_guild(guild=guild)
        except Exception:
            # Unexpected failure: drop stamp only if we have nothing useful left.
            if guild.id not in AUTOCOMPLETE_EVENT_CACHE:
                invalidate_event_option_cache(guild_id=guild.id)
            logger.exception("Event cache warmup failed guild=%s", guild.id)
            schedule_event_cache_retry(
                guild=guild,
                attempt=_next_retry_attempt(retry_attempt=retry_attempt),
            )
            return "failed"

        candidates = loaded.events
        if not loaded.http_ok:
            # Transient network: never stamp a fresh empty over good options.
            if candidates:
                cache_reusable_events_for_guild(guild_id=guild.id, events=candidates)
                logger.warning(
                    "Event cache warmup degraded guild=%s http_ok=false "
                    "cached_gateway_or_partial=%s; scheduling retry",
                    guild.id,
                    len(candidates),
                )
            else:
                logger.warning(
                    "Event cache warmup http failed guild=%s with no fallback events; "
                    "keeping prior options=%s and scheduling retry",
                    guild.id,
                    len(AUTOCOMPLETE_EVENT_CACHE.get(guild.id, [])),
                )
            schedule_event_cache_retry(
                guild=guild,
                attempt=_next_retry_attempt(retry_attempt=retry_attempt),
            )
            return "failed"

        cache_reusable_events_for_guild(guild_id=guild.id, events=candidates)

        if not candidates:
            logger.debug(
                "Event cache warmup: guild=%s, reusable_event=none",
                guild.id,
            )
            return "empty"

        selected = candidates[0]
        logger.debug(
            "Event cache warmup: guild=%s, reusable_event=%s (%s)",
            guild.id,
            selected.id,
            selected.name,
        )
        return "warmed"


async def _warm_guilds(
    *,
    guilds: list[Guild],
    force: bool,
    retry_attempt: int | None = None,
) -> EventCacheWarmResult:
    """Warm many Guilds in parallel under ``warm_guild_concurrency_lock()``."""
    if not guilds:
        return EventCacheWarmResult()

    guild_concurrency = warm_guild_concurrency_lock()

    async def _bounded(guild: Guild) -> WarmOutcome:
        async with guild_concurrency:
            return await _warm_guild(
                guild=guild,
                force=force,
                retry_attempt=retry_attempt,
            )

    outcomes = await asyncio.gather(*(_bounded(guild) for guild in guilds))
    counters = EventCacheWarmResult()
    for outcome in outcomes:
        counters = counters.with_outcome(outcome)
    return counters


async def warmup_connected_event_cache(
    *,
    guilds: list[Guild],
    force: bool = False,
    retry_attempt: int | None = None,
) -> EventCacheWarmResult:
    """Warm picker/autocomplete options. Reuses TTL cache unless ``force``.

    Job / boot entrypoint:
    - boot ``on_ready``: ``force=True`` once so slash autocomplete is hot
    - APScheduler interval job: ``force=True`` before soft TTL expires
    - manual ``/rehydrate``: ``force=True`` for the current guild
    - failure path: invalidate + schedule date retry job

    Guilds warm concurrently under ``warm_guild_concurrency_lock()``.
    Per-guild failures leave that guild cold (no fresh stamp).
    """
    async with stopwatch() as elapsed:
        counters = await _warm_guilds(
            guilds=guilds,
            force=force,
            retry_attempt=retry_attempt,
        )
    result = counters.with_elapsed_ms(elapsed.ms)
    if result.failed > 0:
        logger.warning(
            "Event cache warmup complete with failures: cached=%s, empty=%s, "
            "skipped=%s failed=%s force=%s took %.1fms",
            result.warmed,
            result.empty,
            result.skipped,
            result.failed,
            force,
            result.elapsed_ms,
        )
    else:
        logger.debug(
            "Event cache warmup complete: cached=%s, empty=%s, skipped=%s failed=%s "
            "force=%s took %.1fms",
            result.warmed,
            result.empty,
            result.skipped,
            result.failed,
            force,
            result.elapsed_ms,
        )
    return result
