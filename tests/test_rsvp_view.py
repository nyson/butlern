"""Tests for RSVP view behavior that should remain stable across refactors."""

from __future__ import annotations

from unittest.mock import MagicMock

from butler.rsvp.rsvp_view import AvailabilityView
from butler.rsvp.types import RoomState, ViewState


def _build_view(
    *,
    room_state: RoomState = "pending",
    room_url: str | None = None,
) -> AvailabilityView:
    return AvailabilityView(
        view_state=ViewState(
            event_name="Game Night",
            start_unix=1_700_000_000,
            event_url="https://discord.com/events/1/2",
            edition="Custom",
            edition_emoji=None,
            room_state=room_state,
            room_url=room_url,
            edition_image_url=None,
            event_description="desc",
        ),
        settings_store=MagicMock(),
        view_store=MagicMock(),
        timeout=None,
    )


async def test_set_user_response_keeps_arrival_time_when_switching_non_cant_status() -> None:
    view = _build_view()
    await view.set_user_response(user_id=1, status="Available", arrival_time="20:30")

    await view.set_user_response(user_id=1, status="Maybe")

    response = view._get_response_or_default(1)
    assert response.status == "Maybe"
    assert response.arrival_time == "20:30"


async def test_set_user_response_cant_clears_arrival_time() -> None:
    view = _build_view()
    await view.set_user_response(user_id=1, status="Available", arrival_time="20:30")

    await view.set_user_response(user_id=1, status="Cant")

    response = view._get_response_or_default(1)
    assert response.status == "Cant"
    assert response.arrival_time is None


async def test_set_user_response_after_cant_resets_signup_order() -> None:
    view = _build_view()
    await view.set_user_response(user_id=1, status="Available")
    await view.set_user_response(user_id=2, status="Available")

    assert await view.get_user_ids_for_status("Available") == [1, 2]

    await view.set_user_response(user_id=1, status="Cant")
    await view.set_user_response(user_id=1, status="Available")

    assert await view.get_user_ids_for_status("Available") == [2, 1]


def test_room_action_buttons_use_expected_layout_rows() -> None:
    view = _build_view()

    assert view.prompt_room_link.row == 2
    assert view.close_room_button.row == 2


async def test_room_action_button_visibility_follows_room_state() -> None:
    view = _build_view(room_state="pending")

    assert view.prompt_room_link in view.children
    assert view.close_room_button not in view.children

    await view.set_room_url("https://example.com/room")
    assert view.prompt_room_link not in view.children
    assert view.close_room_button in view.children

    await view.close_room()
    assert view.prompt_room_link in view.children
    assert view.close_room_button not in view.children
