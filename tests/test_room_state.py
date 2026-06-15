"""Unit tests for the pure room-state machine (`butler.rsvp.rsvp_domain`)."""

from __future__ import annotations

from butler.rsvp.rsvp_domain import RoomSnapshot, visible_room_buttons

# --- RoomSnapshot transitions ----------------------------------------------


def test_from_url_opens_room_with_url() -> None:
    snapshot = RoomSnapshot.from_url("https://example.com/room")
    assert snapshot.state == "open"
    assert snapshot.url == "https://example.com/room"


def test_from_url_none_reverts_to_pending() -> None:
    snapshot = RoomSnapshot.from_url(None)
    assert snapshot.state == "pending"
    assert snapshot.url is None


def test_closed_clears_url() -> None:
    snapshot = RoomSnapshot.closed()
    assert snapshot.state == "closed"
    assert snapshot.url is None


# --- visible_room_buttons ---------------------------------------------------


def test_open_shows_only_close() -> None:
    assert visible_room_buttons("open") == frozenset({"close"})


def test_pending_shows_only_open_or_prompt() -> None:
    assert visible_room_buttons("pending") == frozenset({"open_or_prompt"})


def test_closed_shows_only_open_or_prompt() -> None:
    assert visible_room_buttons("closed") == frozenset({"open_or_prompt"})
