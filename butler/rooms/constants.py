from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

# How often botc join pages are re-checked for attached RSVP rooms.
ROOM_POLL_INTERVAL_SECONDS: Final[float] = 300.0


def next_room_check_iso(*, now: datetime | None = None) -> str:
    """UTC ISO timestamp for the next expected room poll after ``now``."""
    base = now if now is not None else datetime.now(UTC)
    base = base.replace(tzinfo=UTC) if base.tzinfo is None else base.astimezone(UTC)
    return (base + timedelta(seconds=ROOM_POLL_INTERVAL_SECONDS)).isoformat()
