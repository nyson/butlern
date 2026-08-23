from __future__ import annotations

import asyncio
import logging

from discord import Guild

from butler.caches.events.listing import reusable_scheduled_events_for_guild
from butler.caches.events.store import (
    cache_reusable_events_for_guild,
    event_option_cache_is_fresh,
)
from butler.caches.events.timing import stopwatch

logger = logging.getLogger(__name__)

_WARMUP_LOCKS: dict[int, asyncio.Lock] = {}


def warmup_lock_for(guild_id: int) -> asyncio.Lock:
    lock = _WARMUP_LOCKS.get(guild_id)
    if lock is None:
        lock = asyncio.Lock()
        _WARMUP_LOCKS[guild_id] = lock
    return lock


async def warmup_connected_event_cache(
    *,
    guilds: list[Guild],
    force: bool = False,
) -> None:
    """Warm picker/autocomplete options. Reuses TTL cache unless ``force``.

    Job / boot entrypoint:
    - boot ``on_ready``: ``force=True`` once so slash autocomplete is hot
    - daily interval job: ``force=True`` to repair drift
    - cold autocomplete path: background ``force=False`` (respects TTL)
    """
    warmed = 0
    empty = 0
    skipped = 0

    async with stopwatch("warmup_connected_event_cache"):
        for guild in guilds:
            if not force and event_option_cache_is_fresh(guild_id=guild.id):
                skipped += 1
                continue
            async with warmup_lock_for(guild.id):
                # Another waiter may have filled the cache while we waited.
                if not force and event_option_cache_is_fresh(guild_id=guild.id):
                    skipped += 1
                    continue
                candidates = await reusable_scheduled_events_for_guild(guild=guild)
                cache_reusable_events_for_guild(
                    guild_id=guild.id,
                    events=candidates,
                )
                if not candidates:
                    empty += 1
                    logger.info(
                        "Event cache warmup: guild=%s, reusable_event=none",
                        guild.id,
                    )
                    continue
                selected = candidates[0]
                warmed += 1
                logger.info(
                    "Event cache warmup: guild=%s, reusable_event=%s (%s)",
                    guild.id,
                    selected.id,
                    selected.name,
                )
    logger.info(
        "Event cache warmup complete: cached=%s, empty=%s, skipped=%s force=%s",
        warmed,
        empty,
        skipped,
        force,
    )
