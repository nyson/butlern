"""Integration checks: rsvp is wired into the bot app module."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

import butler.app as app
from butler.domains.result import Ok
from butler.domains.rsvp.store import RsvpMessageStore
from butler.domains.rsvp.types import ViewState
from butler.rsvp import runtime as rsvp_runtime
from butler.rsvp.controller import RsvpController
from butler.rsvp.view.event_card_view import EventCardView
from butler.rsvp.view.event_message_view import EventMessageView, RoomActions
from butler.rsvp.view.event_select import build_event_select_options


def test_app_exposes_rsvp_controller_and_view_index() -> None:
    assert isinstance(app.RSVP_CONTROLLER, RsvpController)
    assert isinstance(app.ACTIVE_RSVP_VIEWS, dict)
    assert app.RSVP_CONTROLLER._store is app.RSVP_MESSAGE_STORE


def test_event_command_is_registered_on_bot_tree() -> None:
    names = {cmd.name for cmd in app.bot.tree.get_commands()}
    assert "event" in names
    event_group = next(cmd for cmd in app.bot.tree.get_commands() if cmd.name == "event")
    subcommands = {cmd.name: cmd for cmd in event_group.commands}
    assert {"create", "link"}.issubset(subcommands)

    create_opts = {opt.name: opt for opt in cast(Any, subcommands["create"]).parameters}
    assert create_opts["title"].required is True
    assert create_opts["description"].required is True
    assert create_opts["event"].required is False

    link_opts = {opt.name: opt for opt in cast(Any, subcommands["link"]).parameters}
    assert link_opts["event"].required is True
    assert "title" not in link_opts
    assert "description" not in link_opts


async def test_event_view_puts_select_event_on_event_card(tmp_path: Path) -> None:
    store = RsvpMessageStore.load(tmp_path / "v.db")
    controller = RsvpController(store=store)
    state = ViewState(
        event_name="Kvällens spel",
        start_unix=1,
        event_url="https://example.com/event",
        edition=None,
        edition_emoji=None,
        room_state="pending",
        room_url=None,
        edition_image_url=None,
        event_description="desc",
    )
    view = EventMessageView(view_state=state, controller=controller)
    # RSVP LayoutView: body + 3 action rows; link button lives on companion card.
    assert len(view.children) == 4
    room_row = next(child for child in view.children if isinstance(child, RoomActions))
    custom_ids = {cast(Any, child).custom_id for child in room_row.children}
    assert "butler:rsvp:select-event" not in custom_ids
    assert "butler:rsvp:open-room" in custom_ids
    card = view.build_event_card_view()
    assert isinstance(card, EventCardView)
    card_ids = {cast(Any, child).custom_id for child in card.children}
    assert "butler:rsvp:select-event" in card_ids


def test_build_event_select_options_caps_and_labels() -> None:
    choices = [(f"Game {i}", str(i)) for i in range(1, 40)]
    modal_options = build_event_select_options(choices=choices, include_create_new=False)
    assert len(modal_options) == 25
    assert all(opt.value != "__butler_create_new_event__" for opt in modal_options)

    with_create = build_event_select_options(choices=choices, include_create_new=True)
    assert len(with_create) == 25
    assert with_create[0].value == "__butler_create_new_event__"


async def test_controller_update_linked_event(tmp_path: Path) -> None:
    store = RsvpMessageStore.load(tmp_path / "link.db")
    controller = RsvpController(store=store)
    state = ViewState(
        event_name="old",
        start_unix=1,
        event_url="https://example.com/old",
        edition=None,
        edition_emoji=None,
        room_state="pending",
        room_url=None,
        edition_image_url=None,
        event_description="d",
    )
    controller.bind_message(message_id=9, channel_id=1, guild_id=1, view_state=state)
    result = await controller.update_linked_event(
        message_id=9,
        view_state=state,
        event_name="new",
        event_url="https://example.com/new",
        start_unix=99,
    )
    assert isinstance(result, Ok)
    new_state, snap = result.value
    assert new_state.event_name == "new"
    assert new_state.event_url == "https://example.com/new"
    assert new_state.start_unix == 99
    assert snap.view_state.event_name == "new"


async def test_controller_roundtrip_isolated_store(tmp_path: Path) -> None:
    state = ViewState(
        event_name="x",
        start_unix=1,
        event_url="https://example.com",
        edition=None,
        edition_emoji=None,
        room_state="pending",
        room_url=None,
        edition_image_url=None,
        event_description="d",
    )
    ctrl = RsvpController(store=RsvpMessageStore.load(tmp_path / "i.db"))
    ctrl.bind_message(message_id=1, channel_id=2, guild_id=3, view_state=state)
    result = await ctrl.set_status(
        message_id=1,
        view_state=state,
        user_id=9,
        status="Available",
    )
    assert isinstance(result, Ok)
    assert result.value.responses[9].status == "Available"


async def test_apply_snapshot_reregisters_view_after_rebuild(tmp_path: Path) -> None:
    """clear_items orphans ViewStore button refs; rebuild must bot.add_view again."""
    store = RsvpMessageStore.load(tmp_path / "rereg.db")
    controller = RsvpController(store=store)
    state = ViewState(
        event_name="rereg",
        start_unix=1,
        event_url="https://example.com",
        edition=None,
        edition_emoji=None,
        room_state="pending",
        room_url=None,
        edition_image_url=None,
        event_description="d",
        event_card_message_id=77,
    )
    view = EventMessageView(view_state=state, controller=controller)
    view.bind_message_context(message_id=55, channel_id=10, guild_id=1)
    bot = MagicMock()
    bot.add_view = MagicMock()
    view.attach_discord_bot(bot)

    snap = await controller.get_snapshot(message_id=55, view_state=view.view_state)
    bot.add_view.reset_mock()
    view.apply_snapshot(snap)

    registered_ids = {call.kwargs.get("message_id") for call in bot.add_view.call_args_list}
    assert 55 in registered_ids
    assert 77 in registered_ids
    # Fresh children must point back at this LayoutView (not None).
    for item in view.walk_children():
        if getattr(item, "custom_id", None):
            assert item.view is view


async def test_runtime_bind_and_register(tmp_path: Path) -> None:
    store = RsvpMessageStore.load(tmp_path / "rt.db")
    controller = RsvpController(store=store)
    state = ViewState(
        event_name="rt",
        start_unix=1,
        event_url="https://example.com",
        edition=None,
        edition_emoji=None,
        room_state="pending",
        room_url=None,
        edition_image_url=None,
        event_description="d",
    )
    view = EventMessageView(view_state=state, controller=controller)
    view.set_event_card_message_id(9001)
    active: dict[int, EventMessageView] = {}

    class _Msg:
        id = 555

    bot = MagicMock()
    bot.add_view = MagicMock()

    warning = rsvp_runtime.bind_and_register_posted_view(
        view=view,
        message=cast(Any, _Msg()),
        channel_id=10,
        guild_id=1,
        active_views=active,
        bot=bot,
    )
    assert warning is None
    assert 555 in active
    assert active[555] is view
    assert bot.add_view.call_count == 2
    registered_message_ids = {
        call.kwargs.get("message_id") for call in bot.add_view.call_args_list
    }
    assert registered_message_ids == {555, 9001}
    assert store.get_message(555) is not None


async def test_hydrate_persistent_views_registers_from_store_without_fetch(
    tmp_path: Path,
) -> None:
    store = RsvpMessageStore.load(tmp_path / "hydrate.db")
    controller = RsvpController(store=store)
    state = ViewState(
        event_name="hydrated",
        start_unix=1,
        event_url="https://discord.com/events/1/2",
        edition=None,
        edition_emoji=None,
        room_state="pending",
        room_url=None,
        edition_image_url=None,
        event_description="d",
        event_card_message_id=4242,
    )
    controller.bind_message(message_id=42, channel_id=7, guild_id=1, view_state=state)
    await controller.set_status(
        message_id=42,
        view_state=state,
        user_id=9,
        status="Maybe",
    )

    active: dict[int, EventMessageView] = {}
    bot = MagicMock()
    bot.add_view = MagicMock()
    settings = MagicMock()

    done = await rsvp_runtime.hydrate_persistent_views(
        already_hydrated=False,
        active_views=active,
        bot=bot,
        settings_store=settings,
        view_store=store,
        controller=controller,
    )
    assert done is True
    assert 42 in active
    assert active[42].view_state.event_name == "hydrated"
    assert active[42].responses[9].status == "Maybe"
    assert bot.add_view.call_count == 2
    registered_message_ids = {
        call.kwargs.get("message_id") for call in bot.add_view.call_args_list
    }
    assert registered_message_ids == {42, 4242}
    # Second call is a no-op when already hydrated.
    again = await rsvp_runtime.hydrate_persistent_views(
        already_hydrated=True,
        active_views=active,
        bot=bot,
        settings_store=settings,
        view_store=store,
        controller=controller,
    )
    assert again is True
    assert bot.add_view.call_count == 2


async def test_resolve_active_view_rehydrates_and_cleans_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = RsvpMessageStore.load(tmp_path / "resolve.db")
    controller = RsvpController(store=store)
    state = ViewState(
        event_name="lazy",
        start_unix=1,
        event_url="https://example.com",
        edition=None,
        edition_emoji=None,
        room_state="pending",
        room_url=None,
        edition_image_url=None,
        event_description="d",
    )
    controller.bind_message(message_id=77, channel_id=3, guild_id=1, view_state=state)

    active: dict[int, EventMessageView] = {}
    bot = MagicMock()
    bot.add_view = MagicMock()
    settings = MagicMock()

    class _Msg:
        id = 77

    async def _fetch_ok(**_kwargs: object) -> object:
        return _Msg()

    monkeypatch.setattr(rsvp_runtime, "fetch_message_from_channel", _fetch_ok)
    view = await rsvp_runtime.resolve_active_view(
        message_id=77,
        channel_id=3,
        active_views=active,
        bot=bot,
        settings_store=settings,
        view_store=store,
        controller=controller,
    )
    assert view is not None
    assert 77 in active

    active.clear()
    controller.bind_message(message_id=88, channel_id=3, guild_id=1, view_state=state)

    async def _fetch_missing(**_kwargs: object) -> None:
        return None

    monkeypatch.setattr(rsvp_runtime, "fetch_message_from_channel", _fetch_missing)
    missing = await rsvp_runtime.resolve_active_view(
        message_id=88,
        channel_id=3,
        active_views=active,
        bot=bot,
        settings_store=settings,
        view_store=store,
        controller=controller,
    )
    assert missing is None
    assert store.get_message(88) is None
