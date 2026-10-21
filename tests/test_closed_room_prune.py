from __future__ import annotations

from butler.domains.rsvp.domain import (
    apply_botc_snapshot,
    prune_stale_closed_rooms,
    with_room_content_hash,
)
from butler.domains.rsvp.types import RoomEntry
from butler.rooms.constants import ROOM_CLOSED_PRUNE_SECONDS
from butler.rooms.types import BotcJoinSnapshot, BotcPerson


def test_closed_room_section_pruned_after_five_minutes() -> None:
    closed_at = 1_000
    room = with_room_content_hash(
        RoomEntry(
            room_id="r1",
            url="https://botc.app/join/123rum2",
            state="closed",
            label="123rum2",
            source="polled",
            last_checked_unix=closed_at,
            closed_at_unix=closed_at,
        )
    )
    open_room = with_room_content_hash(
        RoomEntry(
            room_id="r2",
            url="https://botc.app/join/123rum",
            state="open",
            label="123rum",
            source="polled",
            last_checked_unix=closed_at,
            botc=BotcJoinSnapshot(
                code="123rum",
                occupancy="open",
                edition="Homebrew",
                phase="Night 1",
                storytellers=(BotcPerson(name="ST"),),
                players=(BotcPerson(name="P1"),),
                player_count=1,
            ),
        )
    )
    rooms = (room, open_room)

    still_visible = prune_stale_closed_rooms(
        rooms,
        now_unix=closed_at + ROOM_CLOSED_PRUNE_SECONDS - 1,
    )
    assert [r.room_id for r in still_visible] == ["r1", "r2"]

    pruned = prune_stale_closed_rooms(
        rooms,
        now_unix=closed_at + ROOM_CLOSED_PRUNE_SECONDS,
    )
    assert [r.room_id for r in pruned] == ["r2"]
    assert ROOM_CLOSED_PRUNE_SECONDS == 5 * 60


def test_closed_room_without_closed_at_is_pruned() -> None:
    room = with_room_content_hash(
        RoomEntry(
            room_id="legacy-closed",
            url="https://botc.app/join/old",
            state="closed",
            source="manual",
        )
    )
    assert prune_stale_closed_rooms((room,), now_unix=1_700_000_000) == ()


def test_botc_close_then_prune_untracks_room() -> None:
    open_snap = BotcJoinSnapshot(
        code="123rum2",
        occupancy="open",
        edition="TB",
        phase="Day",
        storytellers=(BotcPerson(name="ST"),),
        players=(BotcPerson(name="P1"),),
        player_count=1,
    )
    room = with_room_content_hash(
        RoomEntry(
            room_id="r1",
            url="https://botc.app/join/123rum2",
            state="open",
            label="123rum2",
            source="polled",
            last_checked_unix=100,
            botc=open_snap,
        )
    )
    closed_snap = BotcJoinSnapshot(code="123rum2", occupancy="closed")
    closed, _ = apply_botc_snapshot(room, snapshot=closed_snap, now_unix=200)
    assert closed.state == "closed"
    assert closed.closed_at_unix == 200

    kept = prune_stale_closed_rooms((closed,), now_unix=200 + ROOM_CLOSED_PRUNE_SECONDS)
    assert kept == ()
