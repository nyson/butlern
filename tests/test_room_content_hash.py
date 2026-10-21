from butler.domains.rsvp.domain import add_room, apply_botc_snapshot, room_content_hash
from butler.rooms.types import BotcJoinSnapshot, BotcPerson


def test_apply_botc_snapshot_skips_update_when_content_hash_matches() -> None:
    snap = BotcJoinSnapshot(
        code='kaonsdag',
        occupancy='open',
        edition='Trouble Brewing',
        phase='Night',
        storytellers=(BotcPerson(name='ST'),),
        players=(BotcPerson(name='P1'),),
        player_count=1,
    )
    rooms = add_room((), url='https://botc.app/join/kaonsdag', botc=snap, now_unix=10)
    room = rooms[0]
    assert room.content_hash == room_content_hash(room)

    same_snap = BotcJoinSnapshot(
        code='kaonsdag',
        occupancy='open',
        edition='Trouble Brewing',
        phase='Night',
        storytellers=(BotcPerson(name='ST'),),
        players=(BotcPerson(name='P1'),),
        player_count=1,
        open_seats=99,  # not part of visible hash/signature
    )
    updated, opened = apply_botc_snapshot(room, snapshot=same_snap, now_unix=99)
    assert opened is False
    assert updated.content_hash == room.content_hash  # no tracked-field rewrite

    changed = BotcJoinSnapshot(
        code='kaonsdag',
        occupancy='open',
        edition='Trouble Brewing',
        phase='Day',
        storytellers=(BotcPerson(name='ST'),),
        players=(BotcPerson(name='P1'), BotcPerson(name='P2')),
        player_count=2,
    )
    updated2, opened2 = apply_botc_snapshot(room, snapshot=changed, now_unix=100)
    assert opened2 is False
    assert updated2 is not room
    assert updated2.botc is not None
    assert updated2.botc.phase == 'Day'
    assert updated2.content_hash == room_content_hash(updated2)
    assert updated2.content_hash != room.content_hash
