from __future__ import annotations

from typing import Final

MAX_EVENT_AUTOCOMPLETE_CHOICES: Final[int] = 25
MAX_EVENT_CHOICE_NAME_LENGTH: Final[int] = 100
# Soft TTL. Gateway hooks keep the cache live; daily force resync repairs drift.
EVENT_OPTION_CACHE_TTL_SECONDS: Final[float] = 86_400.0
# Delay before first retry of a failed/cold guild event-cache warm.
EVENT_CACHE_RETRY_DELAY_SECONDS: Final[float] = 60.0
# Exponential backoff for repeated transient failures (delay * factor ** (attempt-1)).
EVENT_CACHE_RETRY_BACKOFF_FACTOR: Final[float] = 2.0
EVENT_CACHE_RETRY_MAX_DELAY_SECONDS: Final[float] = 900.0
EVENT_CACHE_RETRY_MAX_ATTEMPTS: Final[int] = 6
# Max Guild objects warming the event cache at once (process-wide).
# Enforced by warmup.warm_guild_concurrency_lock() (shared asyncio.Semaphore).
EVENT_CACHE_WARM_GUILD_CONCURRENCY: Final[int] = 8
