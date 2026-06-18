from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Final

PACKAGE_ROOT: Final[Path] = Path(__file__).resolve().parent
PROJECT_ROOT: Final[Path] = PACKAGE_ROOT.parent

def _path_from_env(env_var: str, *, default_path: Path) -> Path:
    configured_path = os.getenv(env_var)
    if configured_path is None or not configured_path.strip():
        return default_path
    return Path(configured_path)


SETTINGS_PATH: Final[Path] = _path_from_env(
    "BUTLER_DB_PATH",
    default_path=PROJECT_ROOT / "butler_state.db",
)

DEFAULT_EVENT_START_TIME: Final[str] = "19:00"
DEFAULT_EVENT_LOCATION: Final[str] = "Online"
DEFAULT_EVENT_DURATION: Final[dt.timedelta] = dt.timedelta(hours=3)
