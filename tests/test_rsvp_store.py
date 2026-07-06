from __future__ import annotations

import sqlite3
from pathlib import Path

from butler.rsvp.rsvp_domain import RsvpResponse
from butler.rsvp.rsvp_store import RsvpMessageStore
from butler.rsvp.types import ViewState


def _view_state() -> ViewState:
    return ViewState(
        event_name="Game Night",
        start_unix=1_700_000_000,
        event_url="https://discord.com/events/1/2",
        edition="Custom",
        edition_emoji=None,
        room_state="pending",
        room_url=None,
        edition_image_url=None,
        event_description="desc",
    )


def _store(path: Path) -> RsvpMessageStore:
    return RsvpMessageStore.load(path)


def test_message_roundtrip(tmp_path: Path) -> None:
    store = _store(tmp_path / "state.db")
    state = _view_state()

    store.upsert_message(
        message_id=101,
        channel_id=10,
        guild_id=1,
        view_state=state,
    )

    stored = store.get_message(101)
    assert stored is not None
    assert stored.message_id == 101
    assert stored.channel_id == 10
    assert stored.guild_id == 1
    assert stored.view_state == state

    updated_state = ViewState(
        event_name=state.event_name,
        start_unix=state.start_unix,
        event_url=state.event_url,
        edition=state.edition,
        edition_emoji=state.edition_emoji,
        room_state="open",
        room_url="https://example.com/room",
        edition_image_url=state.edition_image_url,
        event_description=state.event_description,
    )
    store.update_view_state(message_id=101, view_state=updated_state)
    updated = store.get_message(101)
    assert updated is not None
    assert updated.view_state.room_state == "open"
    assert updated.view_state.room_url == "https://example.com/room"


def test_response_roundtrip(tmp_path: Path) -> None:
    store = _store(tmp_path / "state.db")
    store.upsert_message(
        message_id=101,
        channel_id=10,
        guild_id=1,
        view_state=_view_state(),
    )

    store.upsert_rsvp_response(
        message_id=101,
        user_id=7,
        response=RsvpResponse(status="Maybe", role="Player", arrival_time=None),
    )
    store.upsert_rsvp_response(
        message_id=101,
        user_id=7,
        response=RsvpResponse(status="Available", role="Storyteller", arrival_time="20:30"),
    )

    response = store.get_rsvp_response(101, 7)
    assert response == RsvpResponse(
        status="Available",
        role="Storyteller",
        arrival_time="20:30",
    )
    assert store.all_responses(101) == {7: response}

    store.remove_rsvp_response(message_id=101, user_id=7)
    assert store.get_rsvp_response(101, 7) is None
    assert store.all_responses(101) == {}


def test_load_rebuilds_incompatible_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE rsvp_message (
                message_id INTEGER PRIMARY KEY,
                event_name TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE rsvp_response (
                message_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                PRIMARY KEY (message_id, user_id)
            )
            """
        )
        connection.commit()

    store = _store(db_path)
    store.upsert_message(
        message_id=202,
        channel_id=20,
        guild_id=2,
        view_state=_view_state(),
    )

    stored = store.get_message(202)
    assert stored is not None
    assert stored.channel_id == 20
