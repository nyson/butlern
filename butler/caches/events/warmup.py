from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Literal

from discord import Guild

from butler.caches.events.constants import EVENT_CACHE_RETRY_DELAY_SECONDS
from butler.caches.events.listing import reusable_scheduled_events_for_guild
from butler.caches.events.option_cache import (
    cache_reusable_events_for_guild,
    event_option_cache_is_fresh,
    invalidate_event_option_cache,
)
from butler.timing import stopwatch

logger = logging.getLogger(__name__)

_WARMUP_LOCKS: dict[int, asyncio.Lock] = {}
_PENDING_RETRIES: dict[int, asyncio.Task[None]] = {}

WarmOutcome = Literal["warmed", "empty", "skipped", "failed"]


@dataclass(frozen=True)
class _WarmupCounters:
    warmed: int = 0
    empty: int = 0
    skipped: int = 0
    failed: int = 0

    def with_warmed(self) -> _WarmupCounters:
        return _WarmupCounters(self.warmed + 1, self.empty, self.skipped, self.failed)

    def with_empty(self) -> _WarmupCounters:
        return _WarmupCounters(self.warmed, self.empty + 1, self.skipped, self.failed)

    def with_skipped(self) -> _WarmupCounters:
        return _WarmupCounters(self.warmed, self.empty, self.skipped + 1, self.failed)

    def with_failed(self) -> _WarmupCounters:
        return _WarmupCounters(self.warmed, self.empty, self.skipped, self.failed + 1)


def warmup_lock_for(guild_id: int) -> asyncio.Lock:
    lock = _WARMUP_LOCKS.get(guild_id)
    if lock is None:
        lock = asyncio.Lock()
        _WARMUP_LOCKS[guild_id] = lock
    return lock


def cancel_pending_event_cache_retries() -> None:
    """Cancel in-flight retry tasks (tests / full cache reset)."""
    pending = list(_PENDING_RETRIES.items())
    _PENDING_RETRIES.clear()
    for _guild_id, task in pending:
        task.cancel()


def event_cache_retry_pending(*, guild_id: int) -> bool:
    task = _PENDING_RETRIES.get(guild_id)
    return task is not None and not task.done()


def schedule_event_cache_retry(*, guild: Guild) -> bool:
    """Schedule a force warm for ``guild`` after the retry delay.

    Dedupes per guild while a retry is already pending. Returns True when a new
    retry was scheduled.
    """
    guild_id = guild.id
    if event_cache_retry_pending(guild_id=guild_id):
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning(
            "Cannot schedule event-cache retry guild=%s: no running event loop",
            guild_id,
        )
        return False

    task = loop.create_task(
        _run_event_cache_retry(guild),
        name=f"event-cache-retry-{guild_id}",
    )
    _PENDING_RETRIES[guild_id] = task
    logger.info(
        "Scheduled event-cache retry guild=%s in %.0fs",
        guild_id,
        EVENT_CACHE_RETRY_DELAY_SECONDS,
    )
    return True


async def _run_event_cache_retry(guild: Guild) -> None:
    guild_id = guild.id
    try:
        await asyncio.sleep(EVENT_CACHE_RETRY_DELAY_SECONDS)
        logger.info("Running event-cache retry warm guild=%s", guild_id)
        await warmup_connected_event_cache(guilds=[guild], force=True)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Event-cache retry warm failed guild=%s", guild_id)
        # Leave cache cold; a later lookup can schedule another retry.
    finally:
        current = _PENDING_RETRIES.get(guild_id)
        if current is asyncio.current_task():
            _PENDING_RETRIES.pop(guild_id, None)


def _should_skip_guild(*, guild_id: int, force: bool) -> bool:
    return not force and event_option_cache_is_fresh(guild_id=guild_id)


async def _warm_guild(*, guild: Guild, force: bool) -> WarmOutcome:
    """Warm one guild. Returns outcome: warmed | empty | skipped | failed."""
    if _should_skip_guild(guild_id=guild.id, force=force):
        return "skipped"

    async with warmup_lock_for(guild.id):
        if _should_skip_guild(guild_id=guild.id, force=force):
            return "skipped"

        try:
            candidates = await reusable_scheduled_events_for_guild(guild=guild)
            cache_reusable_events_for_guild(guild_id=guild.id, events=candidates)
        except Exception:
            # Keep the guild cold/unclean rather than publishing a partial stamp.
            invalidate_event_option_cache(guild_id=guild.id)
            logger.exception("Event cache warmup failed guild=%s", guild.id)
            # Proactively retry after the delay so boot/daily failures recover
            # without waiting for a cold autocomplete hit.
            schedule_event_cache_retry(guild=guild)
            return "failed"

        if not candidates:
            logger.info(
                "Event cache warmup: guild=%s, reusable_event=none",
                guild.id,
            )
            return "empty"

        selected = candidates[0]
        logger.info(
            "Event cache warmup: guild=%s, reusable_event=%s (%s)",
            guild.id,
            selected.id,
            selected.name,
        )
        return "warmed"


async def _warm_guilds(*, guilds: list[Guild], force: bool) -> _WarmupCounters:
    counters = _WarmupCounters()
    for guild in guilds:
        outcome = await _warm_guild(guild=guild, force=force)
        if outcome == "warmed":
            counters = counters.with_warmed()
        elif outcome == "empty":
            counters = counters.with_empty()
        elif outcome == "failed":
            counters = counters.with_failed()
        else:
            counters = counters.with_skipped()
    return counters


async def warmup_connected_event_cache(
    *,
    guilds: list[Guild],
    force: bool = False,
) -> None:
    """Warm picker/autocomplete options. Reuses TTL cache unless ``force``.

    Job / boot entrypoint:
    - boot ``on_ready``: ``force=True`` once so slash autocomplete is hot
    - daily interval job: ``force=True`` to repair drift

    Per-guild failures leave that guild cold (no fresh stamp) so lookups can
    detect the unclean state and schedule a delayed retry.
    """
    async with stopwatch() as elapsed:
        counters = await _warm_guilds(guilds=guilds, force=force)
    logger.info(
        "Event cache warmup complete: cached=%s, empty=%s, skipped=%s failed=%s "
        "force=%s took %.1fms",
        counters.warmed,
        counters.empty,
        counters.skipped,
        counters.failed,
        force,
        elapsed.ms,
    )
