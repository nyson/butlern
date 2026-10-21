from __future__ import annotations

from butler.domains.rsvp.domain import add_room, apply_botc_snapshot, room_content_hash
from butler.rooms.constants import ROOM_EMPTY_ABANDON_SECONDS
from butler.rooms.types import BotcJoinSnapshot, BotcPerson


def _open_snap(
    *,
    phase: str = "Night",
    edition: str = "Trouble Brewing",
    players: tuple[BotcPerson, ...] = (),
    storytellers: tuple[BotcPerson, ...] = (BotcPerson(name="ST"),),
) -> BotcJoinSnapshot:
    return BotcJoinSnapshot(
        code="room1",
        occupancy="open",
        edition=edition,
        phase=phase,
        storytellers=storytellers,
        players=players,
        player_count=len(players),
    )


def test_phase_player_edition_change_updates_hash() -> None:
    snap = _open_snap(players=(BotcPerson(name="P1"),))
    room = add_room((), url="https://botc.app/join/room1", botc=snap, now_unix=10)[0]

    same = apply_botc_snapshot(room, snapshot=snap, now_unix=20)[0]
    assert same.content_hash == room.content_hash

    phase_changed, _ = apply_botc_snapshot(
        room,
        snapshot=_open_snap(phase="Day", players=(BotcPerson(name="P1"),)),
        now_unix=30,
    )
    assert phase_changed.content_hash != room.content_hash

    players_changed, _ = apply_botc_snapshot(
        room,
        snapshot=_open_snap(players=(BotcPerson(name="P1"), BotcPerson(name="P2"))),
        now_unix=40,
    )
    assert players_changed.content_hash != room.content_hash

    edition_changed, _ = apply_botc_snapshot(
        room,
        snapshot=_open_snap(edition="BMR", players=(BotcPerson(name="P1"),)),
        now_unix=50,
    )
    assert edition_changed.content_hash != room.content_hash

    # Storyteller rename alone does not change tracked hash.
    st_renamed, _ = apply_botc_snapshot(
        room,
        snapshot=_open_snap(
            players=(BotcPerson(name="P1"),),
            storytellers=(BotcPerson(name="Other ST"),),
        ),
        now_unix=60,
    )
    assert st_renamed.content_hash == room.content_hash


def test_botc_closed_abandons_room() -> None:
    snap = _open_snap(players=(BotcPerson(name="P1"),))
    room = add_room((), url="https://botc.app/join/room1", botc=snap, now_unix=10)[0]
    closed = BotcJoinSnapshot(code="room1", occupancy="closed")
    updated, opened = apply_botc_snapshot(room, snapshot=closed, now_unix=20)
    assert opened is False
    assert updated.state == "closed"
    assert updated.botc is None
    assert updated.empty_since_unix is None


def test_empty_room_abandons_after_five_minutes() -> None:
    empty = _open_snap(players=(), storytellers=())
    room = add_room((), url="https://botc.app/join/room1", botc=empty, now_unix=100)[0]
    assert room.state == "open"

    still_open, _ = apply_botc_snapshot(room, snapshot=empty, now_unix=100 + 60)
    assert still_open.state == "open"
    assert still_open.empty_since_unix == 100

    abandoned, _ = apply_botc_snapshot(
        still_open,
        snapshot=empty,
        now_unix=100 + ROOM_EMPTY_ABANDON_SECONDS,
    )
    assert abandoned.state == "closed"
    assert abandoned.empty_since_unix is None


def test_empty_timer_resets_when_players_join() -> None:
    empty = _open_snap(players=(), storytellers=())
    room = add_room((), url="https://botc.app/join/room1", botc=empty, now_unix=100)[0]
    waiting, _ = apply_botc_snapshot(room, snapshot=empty, now_unix=200)
    assert waiting.empty_since_unix == 100

    filled = _open_snap(players=(BotcPerson(name="P1"),))
    active, opened = apply_botc_snapshot(waiting, snapshot=filled, now_unix=250)
    assert opened is False
    assert active.state == "open"
    assert active.empty_since_unix is None
    assert room_content_hash(active) == active.content_hash
