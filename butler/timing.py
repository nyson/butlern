from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


@asynccontextmanager
async def stopwatch(
    label: str,
    *,
    guild_id: int | None = None,
) -> AsyncIterator[None]:
    """Log wall time for an async section."""
    started = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if guild_id is None:
            logger.info("%s took %.1fms", label, elapsed_ms)
        else:
            logger.info("%s guild=%s took %.1fms", label, guild_id, elapsed_ms)
