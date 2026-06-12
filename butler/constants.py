from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Final

PACKAGE_ROOT: Final[Path] = Path(__file__).resolve().parent
PROJECT_ROOT: Final[Path] = PACKAGE_ROOT.parent

CONFIG_PATH: Final[Path] = PROJECT_ROOT / "config.toml"
SETTINGS_PATH: Final[Path] = PROJECT_ROOT / "guild_settings.json"

DEFAULT_EVENT_START_TIME: Final[str] = "19:00"
DEFAULT_EVENT_LOCATION: Final[str] = "Online"
DEFAULT_EVENT_DURATION: Final[dt.timedelta] = dt.timedelta(hours=3)
