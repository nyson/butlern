from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class EventInput:
    title: str
    description: str
    room_url: str | None
    start_local: dt.datetime
    start_utc: dt.datetime
    end_utc: dt.datetime


def parse_time_today(time_text: str, now_local: dt.datetime) -> dt.datetime | None:
    try:
        parsed_time = dt.datetime.strptime(time_text, "%H:%M").time()
    except ValueError:
        return None
    return dt.datetime.combine(now_local.date(), parsed_time, tzinfo=now_local.tzinfo)


def normalize_room_url(room_link: str | None) -> str | None:
    if room_link is None:
        return None

    normalized = room_link.strip()
    if not normalized:
        return None

    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("`room_link` must be a full URL starting with `http://` or `https://`.")
    return normalized


def require_non_empty(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"`{field_name}` is required.")
    return normalized


def resolve_event_input(
    *,
    title: str,
    description: str,
    room_link: str | None,
    start_time: str | None,
    now_local: dt.datetime,
    default_start_time: str,
    default_duration: dt.timedelta,
) -> EventInput:
    normalized_title = require_non_empty(title, "title")
    normalized_description = require_non_empty(description, "description")
    normalized_room_url = normalize_room_url(room_link)

    resolved_start_time = (start_time or "").strip() or default_start_time
    used_default_start_time = not start_time or not start_time.strip()
    start_local = parse_time_today(resolved_start_time, now_local)
    if start_local is None:
        raise ValueError("Invalid time format. Use `HH:MM` (for example `18:30`).")

    if start_local <= now_local:
        if used_default_start_time:
            start_local += dt.timedelta(days=1)
        else:
            raise ValueError("That time already passed today. Choose a later time.")

    start_utc = start_local.astimezone(dt.UTC)
    end_utc = start_utc + default_duration
    return EventInput(
        title=normalized_title,
        description=normalized_description,
        room_url=normalized_room_url,
        start_local=start_local,
        start_utc=start_utc,
        end_utc=end_utc,
    )
