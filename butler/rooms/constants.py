from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

# How often botc join pages are re-checked for attached RSVP rooms.
ROOM_POLL_INTERVAL_SECONDS: Final[float] = 60.0

# Open room with 0 players and 0 storytellers for this long is abandoned.
ROOM_EMPTY_ABANDON_SECONDS: Final[int] = 5 * 60

# Closed rooms older than this are not re-pulled (no reopen watch).
ROOM_REOPEN_WATCH_SECONDS: Final[int] = 6 * 60 * 60

# Pause between Discord message edits in a poll batch (channel sub-ratelimit).
ROOM_DISCORD_EDIT_GAP_SECONDS: Final[float] = 1.0

ROOM_POLL_JOB_ID: Final[str] = "room-poll-interval"


def next_room_check_iso(*, now: datetime | None = None) -> str:
    """UTC ISO timestamp for the next expected room poll after ``now``."""
    base = now if now is not None else datetime.now(UTC)
    base = base.replace(tzinfo=UTC) if base.tzinfo is None else base.astimezone(UTC)
    return (base + timedelta(seconds=ROOM_POLL_INTERVAL_SECONDS)).isoformat()
