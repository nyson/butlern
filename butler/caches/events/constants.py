from __future__ import annotations

from typing import Final

MAX_EVENT_AUTOCOMPLETE_CHOICES: Final[int] = 25
MAX_EVENT_CHOICE_NAME_LENGTH: Final[int] = 100
# Soft TTL. Gateway hooks keep the cache live; daily force resync repairs drift.
EVENT_OPTION_CACHE_TTL_SECONDS: Final[float] = 86_400.0
