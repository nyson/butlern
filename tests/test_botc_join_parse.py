from __future__ import annotations

from butler.rooms.parse import parse_botc_join_html
from butler.rooms.urls import botc_join_code, is_botc_join_url, normalize_botc_join_url

CLOSED_HTML = (
    "<html><head><title>Game Session 'kaonsdag'</title>"
    '<meta content="Blood on the Clocktower Online" property="og:site_name"/>'
    "<meta content=\"Game Session 'kaonsdag'\" property=\"og:title\"/>"
    '<meta content="The worldwide smash hit social deduction game is online. "'
    'Currently under construction." property="og:description"/>'
    "</head><body><div>Loading session:</div></body></html>"
)

OPEN_HTML = """\
<html><head><title>Private Game Session 'exampleopen' (10 open seats)</title>\
<meta content="Private Game Session 'exampleopen' (10 open seats)" property="og:title"/>\
<meta content="Chat language: English
Edition: Trouble Brewing
Phase: ⌛ in between games
Storyteller(s): Skrå!
11 Player(s):
 ⚪ Seat 1,
You can join this session with a free account!" property="og:description"/>\
</head><body><div>Loading session:</div>\
<ul class="storytellers"><li id="7554072936602">Skrå!</li></ul>\
<ul class="players">\
<li>⚪ Seat 1</li>\
<li id="8305049829530">⚪ Skråskådaren</li>\
<li>Seat 5</li>\
</ul></body></html>
"""


def test_is_botc_join_url() -> None:
    assert is_botc_join_url("https://botc.app/join/exampleopen")
    assert is_botc_join_url("https://www.botc.app/join/foo")
    assert not is_botc_join_url("https://example.com/join/foo")
    assert botc_join_code("https://botc.app/join/kaonsdag") == "kaonsdag"
    assert normalize_botc_join_url("http://botc.app/join/x/extra") == "https://botc.app/join/x"


def test_parse_closed_join_page() -> None:
    snapshot = parse_botc_join_html(CLOSED_HTML, code="kaonsdag")
    assert snapshot.occupancy == "closed"
    assert snapshot.code == "kaonsdag"
    assert snapshot.open_seats is None
    assert snapshot.edition is None
    assert snapshot.phase is None
    assert snapshot.storytellers == ()
    assert snapshot.players == ()


def test_parse_open_join_page() -> None:
    snapshot = parse_botc_join_html(OPEN_HTML, code="exampleopen")
    assert snapshot.occupancy == "open"
    assert snapshot.is_private is True
    assert snapshot.open_seats == 10
    assert snapshot.edition == "Trouble Brewing"
    assert snapshot.phase == "⌛ in between games"
    assert snapshot.player_count == 11
    assert len(snapshot.storytellers) == 1
    assert snapshot.storytellers[0].name == "Skrå!"
    assert snapshot.storytellers[0].external_id == "7554072936602"
    filled = snapshot.filled_players
    assert [person.name for person in filled] == ["Skråskådaren"]
    assert filled[0].external_id == "8305049829530"
    empty = [person for person in snapshot.players if person.is_empty_seat]
    assert {person.name for person in empty} == {"Seat 1", "Seat 5"}
