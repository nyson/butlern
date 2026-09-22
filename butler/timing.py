from __future__ import annotations

import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass


@dataclass
class Elapsed:
    """Mutable wall-time result filled when the ``stopwatch`` context exits."""

    ms: float = 0.0


@asynccontextmanager
async def stopwatch() -> AsyncGenerator[Elapsed]:
    """Measure wall time for an async section; caller owns any logging."""
    started = time.perf_counter()
    elapsed = Elapsed()
    try:
        yield elapsed
    finally:
        elapsed.ms = (time.perf_counter() - started) * 1000.0
