"""Unit tests for rsvp2 controller + pure render snapshot + thin views."""

from __future__ import annotations

from typing import Any, cast

from pathlib import Path

import pytest

from butler.design import AVAILABLE_EMOJI, RSVP_FOOTER_TEXT
from butler.domains.result import Ok
from butler.domains.rsvp.store import RsvpMessageStore
from butler.domains.rsvp.types import ViewState
from butler.rsvp2.controller import RsvpController
from butler.rsvp2.modals.select_event import SelectEventModal
from butler.rsvp2.view.body import format_rsvp_body
from butler.rsvp2.view.event_message_view import (
    EventMessageView,
    RoomActions,
    RsvpMetaActions,
    RsvpStatusActions,
)


def _view_state(**overrides: object) -> ViewState:
    base: dict[str, object] = {
        "event_name": "Game Night",
        "start_unix": 1_700_000_000,
        "event_url": "https://discord.com/events/1/2",
        "edition": "Custom",
        "edition_emoji": "🎲",
        "room_state": "pending",
        "room_url": None,
        "edition_image_url": None,
        "event_description": "desc",
    }
    base.update(overrides)
    return ViewState(**base)  # type: ignore[arg-type]


def _controller(tmp_path: Path) -> RsvpController:
    return RsvpController(store=RsvpMessageStore.load(tmp_path / "rsvp2.db"))


@pytest.fixture
def controller(tmp_path: Path) -> RsvpController:
    return _controller(tmp_path)


@pytest.fixture
def state() -> ViewState:
    return _view_state()


async def test_set_status_persists_and_snapshots(
    controller: RsvpController,
    state: ViewState,
) -> None:
    controller.bind_message(
        message_id=101,
        channel_id=10,
        guild_id=1,
        view_state=state,
    )
    result = await controller.set_status(
        message_id=101,
        view_state=state,
        user_id=7,
        status="Maybe",
    )
    assert isinstance(result, Ok)
    snapshot = result.value
    assert snapshot.responses[7].status == "Maybe"
    assert snapshot.responses[7].role == "Player"
    stored = controller._store.get_rsvp_response(101, 7)
    assert stored is not None
    assert stored.status == "Maybe"


async def test_cant_clears_arrival_and_storyteller_role(
    controller: RsvpController,
    state: ViewState,
) -> None:
    controller.bind_message(
        message_id=101,
        channel_id=10,
        guild_id=1,
        view_state=state,
    )
    await controller.set_status(
        message_id=101,
        view_state=state,
        user_id=7,
        status="Available",
        arrival_time="21:00",
    )
    await controller.set_storyteller_role(
        message_id=101,
        view_state=state,
        user_id=7,
        is_storyteller=True,
    )
    result = await controller.set_status(
        message_id=101,
        view_state=state,
        user_id=7,
        status="Cant",
    )
    assert isinstance(result, Ok)
    response = result.value.responses[7]
    assert response.status == "Cant"
    assert response.role == "Player"
    assert response.arrival_time is None


async def test_toggle_storyteller_and_arrival_time(
    controller: RsvpController,
    state: ViewState,
) -> None:
    controller.bind_message(
        message_id=101,
        channel_id=10,
        guild_id=1,
        view_state=state,
    )
    result = await controller.toggle_storyteller(
        message_id=101,
        view_state=state,
        user_id=7,
    )
    assert isinstance(result, Ok)
    snap = result.value
    assert snap.responses[7].role == "Storyteller"
    assert snap.responses[7].status == "Available"

    result = await controller.set_arrival_time(
        message_id=101,
        view_state=state,
        user_id=7,
        arrival_time="20:15",
    )
    assert isinstance(result, Ok)
    snap = result.value
    assert snap.responses[7].arrival_time == "20:15"

    result = await controller.toggle_storyteller(
        message_id=101,
        view_state=state,
        user_id=7,
    )
    assert isinstance(result, Ok)
    snap = result.value
    assert snap.responses[7].role == "Player"
    assert snap.responses[7].arrival_time == "20:15"


async def test_arrival_time_from_cant_promotes_available(
    controller: RsvpController,
    state: ViewState,
) -> None:
    controller.bind_message(
        message_id=101,
        channel_id=10,
        guild_id=1,
        view_state=state,
    )
    await controller.set_status(
        message_id=101,
        view_state=state,
        user_id=7,
        status="Cant",
    )
    result = await controller.set_arrival_time(
        message_id=101,
        view_state=state,
        user_id=7,
        arrival_time="19:30",
    )
    assert isinstance(result, Ok)
    snap = result.value
    assert snap.responses[7].status == "Available"
    assert snap.responses[7].arrival_time == "19:30"


async def test_open_and_close_room_update_view_state(
    controller: RsvpController,
    state: ViewState,
) -> None:
    controller.bind_message(
        message_id=101,
        channel_id=10,
        guild_id=1,
        view_state=state,
    )
    result = await controller.open_room(
        message_id=101,
        view_state=state,
        room_url="https://example.com/room",
    )
    assert isinstance(result, Ok)
    new_state, snap = result.value
    assert new_state.room_state == "open"
    assert new_state.room_url == "https://example.com/room"
    assert snap.view_state.room_state == "open"
    assert snap.room_url == "https://example.com/room"

    closed_result = await controller.close_room(
        message_id=101,
        view_state=new_state,
    )
    assert isinstance(closed_result, Ok)
    closed_state, closed_snap = closed_result.value
    assert closed_state.room_state == "closed"
    assert closed_state.room_url is None
    assert closed_snap.room_url is None
    stored = controller._store.get_message(101)
    assert stored is not None
    assert stored.view_state.room_state == "closed"


async def test_remove_response(
    controller: RsvpController,
    state: ViewState,
) -> None:
    controller.bind_message(
        message_id=101,
        channel_id=10,
        guild_id=1,
        view_state=state,
    )
    await controller.set_status(
        message_id=101,
        view_state=state,
        user_id=7,
        status="Maybe",
    )
    result = await controller.remove_response(
        message_id=101,
        view_state=state,
        user_id=7,
    )
    assert isinstance(result, Ok)
    snap = result.value
    assert 7 not in snap.responses
    assert controller._store.get_rsvp_response(101, 7) is None


async def test_ephemeral_then_bind_flushes_pending(
    controller: RsvpController,
    state: ViewState,
) -> None:
    await controller.set_status(
        message_id=None,
        view_state=state,
        user_id=9,
        status="Available",
    )
    snap = await controller.get_snapshot(message_id=None, view_state=state)
    assert snap.responses[9].status == "Available"

    controller.bind_message(
        message_id=202,
        channel_id=10,
        guild_id=1,
        view_state=state,
    )
    await controller.set_status(
        message_id=202,
        view_state=state,
        user_id=9,
        status="Maybe",
    )
    assert controller._store.get_rsvp_response(202, 9) is not None


async def test_user_ids_for_status(
    controller: RsvpController,
    state: ViewState,
) -> None:
    controller.bind_message(
        message_id=101,
        channel_id=10,
        guild_id=1,
        view_state=state,
    )
    await controller.set_status(
        message_id=101,
        view_state=state,
        user_id=1,
        status="Available",
    )
    await controller.set_status(
        message_id=101,
        view_state=state,
        user_id=2,
        status="Maybe",
    )
    await controller.set_status(
        message_id=101,
        view_state=state,
        user_id=3,
        status="Available",
    )
    ids = await controller.user_ids_for_status(
        message_id=101,
        view_state=state,
        status="Available",
    )
    assert sorted(ids) == [1, 3]


async def test_render_includes_title_statuses_and_footer(
    controller: RsvpController,
    state: ViewState,
) -> None:
    controller.bind_message(
        message_id=101,
        channel_id=10,
        guild_id=1,
        view_state=state,
    )
    result = await controller.set_status(
        message_id=101,
        view_state=state,
        user_id=7,
        status="Available",
        arrival_time="21:00",
    )
    assert isinstance(result, Ok)
    content = format_rsvp_body(result.value)
    assert "🎲 Game Night" in content
    assert "desc" in content
    assert AVAILABLE_EMOJI in content
    assert "<@7> (21:00)" in content
    assert RSVP_FOOTER_TEXT in content


async def test_view_layout_is_body_plus_three_action_rows(
    controller: RsvpController,
) -> None:
    linked = _view_state(event_url="https://discord.com/events/1/2")
    view = EventMessageView(view_state=linked, controller=controller)
    # Event card lives on a companion message, not inside the LayoutView.
    assert len(view.children) == 4
    from butler.rsvp2.view.body import RsvpBodyDisplay

    assert isinstance(view.children[0], RsvpBodyDisplay)
    assert isinstance(view.children[1], RsvpStatusActions)
    assert isinstance(view.children[2], RsvpMetaActions)
    assert isinstance(view.children[3], RoomActions)


async def test_render_room_open_line(
    controller: RsvpController,
    state: ViewState,
) -> None:
    controller.bind_message(
        message_id=101,
        channel_id=10,
        guild_id=1,
        view_state=state,
    )
    result = await controller.open_room(
        message_id=101,
        view_state=state,
        room_url="https://example.com/room",
    )
    assert isinstance(result, Ok)
    _new_state, snap = result.value
    content = format_rsvp_body(snap)
    assert "https://example.com/room" in content


def _room_actions(view: EventMessageView) -> RoomActions:
    for child in view.children:
        if isinstance(child, RoomActions):
            return child
    raise AssertionError("RoomActions row missing from view")


async def test_view_apply_snapshot_builds_action_rows(
    controller: RsvpController,
    state: ViewState,
) -> None:
    view = EventMessageView(view_state=state, controller=controller)
    assert len(view.children) == 4
    assert isinstance(view.children[1], RsvpStatusActions)
    assert isinstance(view.children[2], RsvpMetaActions)
    assert isinstance(view.children[3], RoomActions)
    room_ids = {cast(Any, child).custom_id for child in _room_actions(view).children}
    assert "butler:rsvp2:select-event" in room_ids


async def test_view_apply_snapshot_is_idempotent(
    controller: RsvpController,
    state: ViewState,
) -> None:
    view = EventMessageView(view_state=state, controller=controller)
    snap = await controller.get_snapshot(message_id=None, view_state=state)
    view.apply_snapshot(snap)
    view.apply_snapshot(snap)
    assert len(view.children) == 4


async def test_room_actions_visibility_pending_vs_open(
    controller: RsvpController,
    state: ViewState,
) -> None:
    view = EventMessageView(view_state=state, controller=controller)
    room_row = _room_actions(view)
    labels = [cast(Any, child).label for child in room_row.children]
    assert any(label and "Öppna" in label for label in labels)
    assert any(label and "Discord-event" in label for label in labels)

    result = await controller.open_room(
        message_id=None,
        view_state=state,
        room_url="https://example.com/room",
    )
    assert isinstance(result, Ok)
    new_state, snap = result.value
    view.view_state = new_state
    view.apply_snapshot(snap)
    room_row = _room_actions(view)
    labels = [cast(Any, child).label for child in room_row.children]
    assert any(label and "Stäng" in label for label in labels)
    assert any(label and "Discord-event" in label for label in labels)


async def test_view_bind_message_context_persists(
    controller: RsvpController,
    state: ViewState,
) -> None:
    view = EventMessageView(view_state=state, controller=controller)
    view.bind_message_context(message_id=303, channel_id=10, guild_id=1)
    assert view.message_id == 303
    stored = controller._store.get_message(303)
    assert stored is not None
    assert stored.view_state.event_name == "Game Night"


async def test_view_set_status_defers_and_updates(
    controller: RsvpController,
    state: ViewState,
) -> None:
    from tests.fakes import FakeInteraction, FakeMember

    view = EventMessageView(view_state=state, controller=controller)
    view.bind_message_context(message_id=404, channel_id=10, guild_id=1)
    ix = FakeInteraction(user=FakeMember(id=42))
    ix.message = None  # type: ignore[attr-defined]
    await view.set_status(ix, "Maybe")  # type: ignore[arg-type]
    assert ix.response.deferred is True
    stored = controller._store.get_rsvp_response(404, 42)
    assert stored is not None
    assert stored.status == "Maybe"
    assert len(view.children) == 4


async def test_open_event_select_sends_modal_from_warm_cache(
    controller: RsvpController,
    state: ViewState,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from butler.caches.events import AUTOCOMPLETE_EVENT_CACHE
    from butler.rsvp2.view import event_message_view as emv
    from tests.fakes import FakeGuild, FakeInteraction, FakeMember

    monkeypatch.setattr(emv, "can_manage_room_action", lambda *_a, **_k: True)
    guild = FakeGuild(guild_id=99)
    user = FakeMember(id=7)
    view = EventMessageView(view_state=state, controller=controller)
    view.bind_message_context(message_id=500, channel_id=10, guild_id=99)
    ix = FakeInteraction(guild=guild, user=user)
    ix.message = None  # type: ignore[attr-defined]

    previous = AUTOCOMPLETE_EVENT_CACHE.get(99)
    AUTOCOMPLETE_EVENT_CACHE[99] = [("Warm Event", "123")]
    try:
        await view.open_event_select(ix)  # type: ignore[arg-type]
    finally:
        if previous is None:
            AUTOCOMPLETE_EVENT_CACHE.pop(99, None)
        else:
            AUTOCOMPLETE_EVENT_CACHE[99] = previous

    assert len(ix.response.modals) == 1
    assert isinstance(ix.response.modals[0], SelectEventModal)


async def test_controller_then_event_message_view_import_has_no_cycle() -> None:
    import importlib
    import sys

    # Ensure a clean import order: controller must load without any view package.
    for name in list(sys.modules):
        if name == "butler.rsvp2.controller" or name.startswith("butler.rsvp2.view"):
            del sys.modules[name]

    controller_mod = importlib.import_module("butler.rsvp2.controller")
    assert hasattr(controller_mod, "RsvpController")
    assert not any(name.startswith("butler.rsvp2.view") for name in sys.modules)

    view_mod = importlib.import_module("butler.rsvp2.view.event_message_view")
    assert hasattr(view_mod, "EventMessageView")
    assert controller_mod.RsvpRenderSnapshot is view_mod.RsvpRenderSnapshot


def _module_imports_rsvp_store(path: Path) -> bool:
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "butler.domains.rsvp.store":
            return True
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "butler.domains.rsvp.store":
                    return True
    return False


async def test_presentation_modules_do_not_import_store() -> None:
    roots = [
        Path("butler/rsvp2/view"),
        Path("butler/rsvp2/modals"),
    ]
    offenders = [
        str(path)
        for root in roots
        for path in root.rglob("*.py")
        if _module_imports_rsvp_store(path)
    ]
    assert offenders == []
