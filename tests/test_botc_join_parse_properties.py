from __future__ import annotations

import html
from dataclasses import dataclass

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from butler.rooms.parse import parse_botc_join_html
from butler.rooms.types import BotcJoinSnapshot, BotcOccupancy, BotcPerson

# Characters safe inside single-quoted title codes and plain text names.
_CODE_CHARS = st.sampled_from("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
_NAME_CHARS = st.sampled_from(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 !?.-_åäöÅÄÖ"
)
_SEAT_GLYPHS = st.sampled_from(["", "⚪ ", "⚫ ", "○ ", "● ", "\u26aa ", "\u26ab "])


@st.composite
def session_codes(draw: st.DrawFn) -> str:
    return draw(st.text(_CODE_CHARS, min_size=1, max_size=24))


@st.composite
def person_names(draw: st.DrawFn) -> str:
    # Non-empty after whitespace collapse; avoid pure seat labels.
    raw = draw(st.text(_NAME_CHARS, min_size=1, max_size=32)).strip()
    assume(raw)
    assume(not raw.casefold().startswith("seat "))
    return " ".join(raw.split())


@st.composite
def external_ids(draw: st.DrawFn) -> str:
    return draw(st.text(st.sampled_from("0123456789"), min_size=1, max_size=16))


@dataclass(frozen=True)
class GeneratedPerson:
    name: str
    external_id: str | None
    is_empty_seat: bool
    li_text: str


@dataclass(frozen=True)
class GeneratedJoinPage:
    code: str
    fallback_code: str
    is_private: bool
    open_seats: int | None
    chat_language: str | None
    edition: str | None
    phase: str | None
    og_player_count: int | None
    og_storyteller_names: tuple[str, ...]
    storytellers: tuple[GeneratedPerson, ...]
    players: tuple[GeneratedPerson, ...]
    closed_marketing: bool
    html: str


def _li(person: GeneratedPerson) -> str:
    name = html.escape(person.li_text)
    if person.external_id is None:
        return f"<li>{name}</li>"
    return f'<li id="{html.escape(person.external_id, quote=True)}">{name}</li>'


def _title(*, code: str, is_private: bool, open_seats: int | None) -> str:
    private = "Private " if is_private else ""
    if open_seats is None:
        seats = ""
    else:
        unit = "seat" if open_seats == 1 else "seats"
        seats = f" ({open_seats} open {unit})"
    return f"{private}Game Session '{code}'{seats}"


def _og_description(
    *,
    closed_marketing: bool,
    chat_language: str | None,
    edition: str | None,
    phase: str | None,
    og_storyteller_names: tuple[str, ...],
    og_player_count: int | None,
) -> str:
    if closed_marketing:
        return (
            "The worldwide smash hit social deduction game is online. Currently under construction."
        )
    lines: list[str] = []
    if chat_language is not None:
        lines.append(f"Chat language: {chat_language}")
    if edition is not None:
        lines.append(f"Edition: {edition}")
    if phase is not None:
        lines.append(f"Phase: {phase}")
    if og_storyteller_names:
        lines.append(f"Storyteller(s): {', '.join(og_storyteller_names)}")
    if og_player_count is not None:
        lines.append(f"{og_player_count} Player(s):")
    lines.append("You can join this session with a free account!")
    return "\n".join(lines)


def _render_join_html(page: GeneratedJoinPage) -> str:
    title = _title(code=page.code, is_private=page.is_private, open_seats=page.open_seats)
    description = _og_description(
        closed_marketing=page.closed_marketing,
        chat_language=page.chat_language,
        edition=page.edition,
        phase=page.phase,
        og_storyteller_names=page.og_storyteller_names,
        og_player_count=page.og_player_count,
    )
    body_parts = ["<div>Loading session:</div>"]
    if page.storytellers:
        items = "".join(_li(person) for person in page.storytellers)
        body_parts.append(f'<ul class="storytellers">{items}</ul>')
    if page.players:
        items = "".join(_li(person) for person in page.players)
        body_parts.append(f'<ul class="players">{items}</ul>')
    return (
        "<html><head>"
        f"<title>{html.escape(title)}</title>"
        f'<meta content="{html.escape(title, quote=True)}" property="og:title"/>'
        f'<meta content="{html.escape(description, quote=True)}" property="og:description"/>'
        "</head><body>"
        f"{''.join(body_parts)}"
        "</body></html>"
    )


@st.composite
def filled_people(draw: st.DrawFn) -> GeneratedPerson:
    name = draw(person_names())
    # Parser treats missing external_id as an empty seat, so filled seats always have one.
    external_id = draw(external_ids())
    glyph = draw(_SEAT_GLYPHS)
    return GeneratedPerson(
        name=name,
        external_id=external_id,
        is_empty_seat=False,
        li_text=f"{glyph}{name}",
    )


@st.composite
def empty_seats(draw: st.DrawFn) -> GeneratedPerson:
    number = draw(st.integers(min_value=1, max_value=20))
    name = f"Seat {number}"
    glyph = draw(_SEAT_GLYPHS)
    # Real pages omit ids on empty seats; seat-name pattern would still mark empty if present.
    return GeneratedPerson(
        name=name,
        external_id=None,
        is_empty_seat=True,
        li_text=f"{glyph}{name}",
    )


@st.composite
def people_lists(draw: st.DrawFn) -> tuple[GeneratedPerson, ...]:
    filled = draw(st.lists(filled_people(), max_size=5, unique_by=lambda p: p.name))
    empty = draw(st.lists(empty_seats(), max_size=5))
    mixed = draw(st.permutations([*filled, *empty]))
    return tuple(mixed)


@st.composite
def optional_label(draw: st.DrawFn) -> str | None:
    label = (
        st.text(_NAME_CHARS, min_size=1, max_size=24)
        .map(lambda s: " ".join(s.split()))
        .filter(bool)
    )
    return draw(st.none() | label)


@st.composite
def closed_join_pages(draw: st.DrawFn) -> GeneratedJoinPage:
    code = draw(session_codes())
    fallback = draw(session_codes())
    is_private = draw(st.booleans())
    page = GeneratedJoinPage(
        code=code,
        fallback_code=fallback,
        is_private=is_private,
        open_seats=None,
        chat_language=None,
        edition=None,
        phase=None,
        og_player_count=None,
        og_storyteller_names=(),
        storytellers=(),
        players=(),
        closed_marketing=True,
        html="",
    )
    return GeneratedJoinPage(**{**page.__dict__, "html": _render_join_html(page)})


@st.composite
def open_join_pages(draw: st.DrawFn) -> GeneratedJoinPage:
    code = draw(session_codes())
    fallback = draw(session_codes())
    is_private = draw(st.booleans())
    open_seats = draw(st.none() | st.integers(min_value=0, max_value=15))
    chat_language = draw(optional_label())
    edition = draw(optional_label())
    phase = draw(optional_label())
    storytellers = draw(st.lists(filled_people(), max_size=3, unique_by=lambda p: p.name))
    players = draw(people_lists())
    og_player_count = draw(st.none() | st.integers(min_value=0, max_value=20))
    og_storyteller_names = tuple(person.name for person in storytellers)

    # Occupancy becomes open only via open seats, roster, edition/phase/player_count.
    # chat_language alone is not enough (matches parse_botc_join_html).
    use_html_roster = draw(st.booleans())
    html_storytellers = tuple(storytellers) if use_html_roster else ()
    html_players = tuple(players) if use_html_roster else ()
    makes_open = any(
        (
            open_seats is not None,
            edition is not None,
            phase is not None,
            og_player_count is not None,
            bool(html_storytellers or html_players),
            bool(og_storyteller_names) and not use_html_roster,
        )
    )
    assume(makes_open)
    page = GeneratedJoinPage(
        code=code,
        fallback_code=fallback,
        is_private=is_private,
        open_seats=open_seats,
        chat_language=chat_language,
        edition=edition,
        phase=phase,
        og_player_count=og_player_count,
        og_storyteller_names=og_storyteller_names,
        storytellers=html_storytellers,
        players=html_players,
        closed_marketing=False,
        html="",
    )
    return GeneratedJoinPage(**{**page.__dict__, "html": _render_join_html(page)})


join_pages = st.one_of(closed_join_pages(), open_join_pages())


def _expected_storytellers(page: GeneratedJoinPage) -> tuple[BotcPerson, ...]:
    if page.storytellers:
        return tuple(
            BotcPerson(
                name=person.name,
                external_id=person.external_id,
                is_empty_seat=False,
            )
            for person in page.storytellers
        )
    return tuple(
        BotcPerson(name=name, external_id=None, is_empty_seat=False)
        for name in page.og_storyteller_names
    )


def _expected_players(page: GeneratedJoinPage) -> tuple[BotcPerson, ...]:
    return tuple(
        BotcPerson(
            name=person.name,
            external_id=person.external_id,
            is_empty_seat=person.is_empty_seat,
        )
        for person in page.players
    )


def _expected_snapshot(page: GeneratedJoinPage) -> BotcJoinSnapshot:
    storytellers = _expected_storytellers(page)
    players = _expected_players(page)
    occupancy: BotcOccupancy
    if page.closed_marketing:
        occupancy = "closed"
        player_count = None
        chat_language = None
        edition = None
        phase = None
    else:
        occupancy = "open"
        player_count = page.og_player_count
        if player_count is None:
            player_count = len(players) if players else None
        chat_language = page.chat_language
        edition = page.edition
        phase = page.phase
    return BotcJoinSnapshot(
        code=page.code,
        occupancy=occupancy,
        is_private=page.is_private,
        open_seats=page.open_seats,
        chat_language=chat_language,
        edition=edition,
        phase=phase,
        storytellers=storytellers,
        players=players,
        player_count=player_count,
    )


@given(page=join_pages)
@settings(max_examples=100, deadline=None)
def test_parse_botc_join_html_matches_generated_page(page: GeneratedJoinPage) -> None:
    snapshot = parse_botc_join_html(page.html, code=page.fallback_code)
    assert snapshot == _expected_snapshot(page)


@given(page=open_join_pages())
@settings(max_examples=50, deadline=None)
def test_open_pages_are_never_closed(page: GeneratedJoinPage) -> None:
    snapshot = parse_botc_join_html(page.html, code=page.fallback_code)
    assert snapshot.occupancy == "open"
    assert snapshot.code == page.code


@given(page=closed_join_pages())
@settings(max_examples=50, deadline=None)
def test_closed_pages_have_no_session_details(page: GeneratedJoinPage) -> None:
    snapshot = parse_botc_join_html(page.html, code=page.fallback_code)
    assert snapshot.occupancy == "closed"
    assert snapshot.open_seats is None
    assert snapshot.edition is None
    assert snapshot.phase is None
    assert snapshot.chat_language is None
    assert snapshot.storytellers == ()
    assert snapshot.players == ()
