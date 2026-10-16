from __future__ import annotations

from urllib.parse import urlparse

BOTC_JOIN_HOSTS = frozenset({"botc.app", "www.botc.app"})


def is_botc_join_url(url: str) -> bool:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").casefold()
    if host not in BOTC_JOIN_HOSTS:
        return False
    parts = [part for part in parsed.path.split("/") if part]
    return len(parts) >= 2 and parts[0].casefold() == "join" and bool(parts[1])


def botc_join_code(url: str) -> str | None:
    if not is_botc_join_url(url):
        return None
    parsed = urlparse(url.strip())
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None
    return parts[1]


def normalize_botc_join_url(url: str) -> str | None:
    code = botc_join_code(url)
    if code is None:
        return None
    return f"https://botc.app/join/{code}"
