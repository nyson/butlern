from __future__ import annotations

import datetime as dt
from typing import Final
from zoneinfo import ZoneInfo

MAX_EVENT_AUTOCOMPLETE_CHOICES: Final[int] = 25
MAX_EVENT_CHOICE_NAME_LENGTH: Final[int] = 100
# Soft TTL for cold paths. Gateway hooks keep the cache live; daily force resync
# repairs drift. Avoid list-events on every picker open.
EVENT_OPTION_CACHE_TTL_SECONDS: Final[float] = 86_400.0
SWEDISH_TIMEZONE: Final[dt.tzinfo] = ZoneInfo("Europe/Stockholm")
