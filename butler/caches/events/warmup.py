from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from discord import Guild

from butler.caches.events.listing import reusable_scheduled_events_for_guild
from butler.caches.events.option_cache import (
    cache_reusable_events_for_guild,
    event_option_cache_is_fresh,
)
from butler.timing import stopwatch

logger = logging.getLogger(__name__)

_WARMUP_LOCKS: dict[int, asyncio.Lock] = {}


@dataclass(frozen=True)
class _WarmupCounters:
    warmed: int = 0
    empty: int = 0
    skipped: int = 0

    def with_warmed(self) -> _WarmupCounters:
        return _WarmupCounters(self.warmed + 1, self.empty, self.skipped)

    def with_empty(self) -> _WarmupCounters:
        return _WarmupCounters(self.warmed, self.empty + 1, self.skipped)

    def with_skipped(self) -> _WarmupCounters:
        return _WarmupCounters(self.warmed, self.empty, self.skipped + 1)


def warmup_lock_for(guild_id: int) -> asyncio.Lock:
    lock = _WARMUP_LOCKS.get(guild_id)
    if lock is None:
        lock = asyncio.Lock()
        _WARMUP_LOCKS[guild_id] = lock
    return lock


def _should_skip_guild(*, guild_id: int, force: bool) -> bool:
    return not force and event_option_cache_is_fresh(guild_id=guild_id)


async def _warm_guild(*, guild: Guild, force: bool) -> str:
    """Warm one guild. Returns outcome: warmed | empty | skipped."""
    if _should_skip_guild(guild_id=guild.id, force=force):
        return "skipped"

    async with warmup_lock_for(guild.id):
        if _should_skip_guild(guild_id=guild.id, force=force):
            return "skipped"

        candidates = await reusable_scheduled_events_for_guild(guild=guild)
        cache_reusable_events_for_guild(guild_id=guild.id, events=candidates)
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
    """
    async with stopwatch("warmup_connected_event_cache"):
        counters = await _warm_guilds(guilds=guilds, force=force)
    logger.info(
        "Event cache warmup complete: cached=%s, empty=%s, skipped=%s force=%s",
        counters.warmed,
        counters.empty,
        counters.skipped,
        force,
    )
