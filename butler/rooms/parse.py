from __future__ import annotations

import re
from html.parser import HTMLParser

from butler.rooms.types import BotcJoinSnapshot, BotcOccupancy, BotcPerson

_OPEN_SEATS_RE = re.compile(r"\((\d+)\s+open seats?\)", re.IGNORECASE)
_PRIVATE_TITLE_RE = re.compile(r"^\s*Private Game Session\b", re.IGNORECASE)
_SESSION_TITLE_RE = re.compile(
    r"^\s*(?:Private\s+)?Game Session\s+'([^']+)'",
    re.IGNORECASE,
)
_SEAT_NAME_RE = re.compile(r"^Seat\s*\d+$", re.IGNORECASE)
_SEAT_GLYPHS_RE = re.compile(r"^[\s\u26aa\u26ab\u25cb\u25cf\u25c9●○⚪⚫]+")


def _clean_person_name(raw: str) -> str:
    text = raw.replace("\t", " ").replace("\n", " ").strip()
    text = _SEAT_GLYPHS_RE.sub("", text).strip()
    return re.sub(r"\s+", " ", text)


class _JoinHtmlParser(HTMLParser):
    """HTML parser that collects title, og tags, and roster lists."""

    def __init__(self) -> None:
        super().__init__()
        self.title_parts: list[str] = []
        self.og_title: str | None = None
        self.og_description: str | None = None
        self._in_title = False
        self._list_kind: str | None = None
        self._in_li = False
        self._li_id: str | None = None
        self._li_parts: list[str] = []
        self.storyteller_items: list[tuple[str | None, str]] = []
        self.player_items: list[tuple[str | None, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        lowered = tag.casefold()
        if lowered == "title":
            self._in_title = True
            return
        if lowered == "meta":
            prop = (attr_map.get("property") or "").casefold()
            content = attr_map.get("content")
            if content is None:
                return
            if prop == "og:title":
                self.og_title = content
            elif prop == "og:description":
                self.og_description = content
            return
        if lowered == "ul":
            class_name = (attr_map.get("class") or "").casefold()
            if "storytellers" in class_name.split():
                self._list_kind = "storytellers"
            elif "players" in class_name.split():
                self._list_kind = "players"
            return
        if lowered == "li" and self._list_kind is not None:
            self._in_li = True
            self._li_id = attr_map.get("id")
            self._li_parts = []

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if lowered == "title":
            self._in_title = False
            return
        if lowered == "ul":
            self._list_kind = None
            return
        if lowered == "li" and self._in_li and self._list_kind is not None:
            name = _clean_person_name("".join(self._li_parts))
            item = (self._li_id, name)
            if self._list_kind == "storytellers":
                self.storyteller_items.append(item)
            else:
                self.player_items.append(item)
            self._in_li = False
            self._li_id = None
            self._li_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if self._in_li:
            self._li_parts.append(data)


def _person_from_item(item: tuple[str | None, str]) -> BotcPerson | None:
    external_id, name = item
    if not name:
        return None
    empty = external_id is None or bool(_SEAT_NAME_RE.match(name))
    return BotcPerson(name=name, external_id=external_id, is_empty_seat=empty)


def _parse_og_description(description: str | None) -> dict[str, object]:
    result: dict[str, object] = {
        "chat_language": None,
        "edition": None,
        "phase": None,
        "storyteller_names": [],
        "player_count": None,
    }
    if description is None:
        return result
    # Generic marketing blurb for closed rooms — no structured fields.
    if "worldwide smash hit" in description.casefold():
        return result

    storyteller_names: list[str] = []
    for raw_line in description.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lower = line.casefold()
        if lower.startswith("chat language:"):
            result["chat_language"] = line.split(":", 1)[1].strip() or None
        elif lower.startswith("edition:"):
            result["edition"] = line.split(":", 1)[1].strip() or None
        elif lower.startswith("phase:"):
            result["phase"] = line.split(":", 1)[1].strip() or None
        elif lower.startswith("storyteller"):
            names_part = line.split(":", 1)[1].strip() if ":" in line else ""
            storyteller_names = [
                name.strip() for name in names_part.split(",") if name.strip()
            ]
        else:
            count_match = re.match(r"^(\d+)\s+player", lower)
            if count_match:
                result["player_count"] = int(count_match.group(1))
    result["storyteller_names"] = storyteller_names
    return result


def _title_text(parser: _JoinHtmlParser) -> str:
    from_tag = "".join(parser.title_parts).strip()
    if from_tag:
        return from_tag
    return (parser.og_title or "").strip()


def _code_from_title(title: str, fallback: str) -> str:
    match = _SESSION_TITLE_RE.search(title)
    if match:
        return match.group(1)
    return fallback


def parse_botc_join_html(html: str, *, code: str) -> BotcJoinSnapshot:
    """Parse botc.app/join HTML into a structured snapshot."""
    parser = _JoinHtmlParser()
    parser.feed(html)
    parser.close()

    title = _title_text(parser)
    resolved_code = _code_from_title(title, code)
    open_seats_match = _OPEN_SEATS_RE.search(title)
    open_seats = int(open_seats_match.group(1)) if open_seats_match else None
    is_private = bool(_PRIVATE_TITLE_RE.search(title))

    storytellers = tuple(
        person
        for person in (_person_from_item(item) for item in parser.storyteller_items)
        if person is not None
    )
    players = tuple(
        person
        for person in (_person_from_item(item) for item in parser.player_items)
        if person is not None
    )

    og = _parse_og_description(parser.og_description)
    raw_storyteller_names = og["storyteller_names"]
    if not storytellers and isinstance(raw_storyteller_names, list):
        storytellers = tuple(
            BotcPerson(name=name, external_id=None, is_empty_seat=False)
            for name in raw_storyteller_names
            if isinstance(name, str)
        )

    has_roster = bool(storytellers or players or parser.storyteller_items or parser.player_items)
    has_session_meta = any(
        (
            og["edition"] is not None,
            og["phase"] is not None,
            og["player_count"] is not None,
            open_seats is not None,
        )
    )

    occupancy: BotcOccupancy
    if open_seats is not None or has_roster or has_session_meta:
        occupancy = "open"
    elif _SESSION_TITLE_RE.search(title) or title:
        occupancy = "closed"
    else:
        occupancy = "unknown"

    player_count = og["player_count"]
    if not isinstance(player_count, int):
        player_count = len(players) if players else None

    edition = og["edition"] if isinstance(og["edition"], str) else None
    phase = og["phase"] if isinstance(og["phase"], str) else None
    chat_language = og["chat_language"] if isinstance(og["chat_language"], str) else None

    return BotcJoinSnapshot(
        code=resolved_code,
        occupancy=occupancy,
        is_private=is_private,
        open_seats=open_seats,
        chat_language=chat_language,
        edition=edition,
        phase=phase,
        storytellers=storytellers,
        players=players,
        player_count=player_count,
    )
