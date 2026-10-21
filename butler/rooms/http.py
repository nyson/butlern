from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

import aiohttp

from butler.rooms.constants import next_room_check_iso
from butler.rooms.parse import parse_botc_join_html
from butler.rooms.types import BotcJoinSnapshot
from butler.rooms.urls import botc_join_code, is_botc_join_url

logger = logging.getLogger(__name__)

_FETCH_TIMEOUT_SECONDS = 8.0
_USER_AGENT = "ButlerDiscordBot/0.1 (+https://github.com/nyson/butler)"


async def fetch_botc_join_snapshot(
    url: str,
    *,
    room_id: str | None = None,
    message_id: int | None = None,
) -> BotcJoinSnapshot | None:
    """GET a botc.app/join page and parse occupancy/roster metadata.

    Returns None on non-botc URLs or transport/parse failure.
    """
    if not is_botc_join_url(url):
        return None
    code = botc_join_code(url)
    if code is None:
        return None

    checked_at = datetime.now(UTC)
    next_check = next_room_check_iso(now=checked_at)
    started = time.perf_counter()
    logger.debug(
        "room pull start url=%s code=%s room_id=%s message_id=%s next_check=%s",
        url,
        code,
        room_id,
        message_id,
        next_check,
    )
    timeout = aiohttp.ClientTimeout(total=_FETCH_TIMEOUT_SECONDS)
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.get(
                url,
                headers={"User-Agent": _USER_AGENT, "Accept": "text/html"},
                allow_redirects=True,
            ) as response,
        ):
            if response.status >= 400:
                logger.warning(
                    "room pull HTTP %s url=%s room_id=%s message_id=%s "
                    "next_check=%s",
                    response.status,
                    url,
                    room_id,
                    message_id,
                    next_check,
                )
                return None
            html = await response.text(errors="replace")
            logger.debug(
                "room pull response url=%s room_id=%s message_id=%s "
                "http=%s bytes=%s",
                url,
                room_id,
                message_id,
                response.status,
                len(html),
            )
    except (TimeoutError, aiohttp.ClientError, OSError):
        logger.warning(
            "room pull failed url=%s room_id=%s message_id=%s next_check=%s",
            url,
            room_id,
            message_id,
            next_check,
            exc_info=True,
        )
        return None

    try:
        snapshot = parse_botc_join_html(html, code=code)
    except Exception:
        logger.exception(
            "room pull parse failed url=%s room_id=%s message_id=%s next_check=%s",
            url,
            room_id,
            message_id,
            next_check,
        )
        return None

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    logger.debug(
        "room pull ok url=%s room_id=%s message_id=%s code=%s occupancy=%s "
        "open_seats=%s edition=%r phase=%r storytellers=%s players_filled=%s "
        "took_ms=%.1f checked_at=%s next_check=%s",
        url,
        room_id,
        message_id,
        snapshot.code,
        snapshot.occupancy,
        snapshot.open_seats,
        snapshot.edition,
        snapshot.phase,
        len(snapshot.storytellers),
        len(snapshot.filled_players),
        elapsed_ms,
        checked_at.isoformat(),
        next_check,
    )
    return snapshot
