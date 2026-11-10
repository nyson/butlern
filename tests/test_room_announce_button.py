"""Per-room Annonsera button + the shared room-link modal (ping dropdown)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import discord
from discord import ui

from butler.design import (
    PLAYER_ROLE_MISSING_MESSAGE,
    ROOM_ANNOUNCE_MODAL_TITLE,
    ROOM_ANNOUNCE_ROOM_GONE_MESSAGE,
    ROOM_LINK_MODAL_TITLE,
)
from butler.domains.rsvp.domain import RsvpResponse
from butler.domains.rsvp.store import RsvpMessageStore
from butler.domains.rsvp.types import RoomEntry, ViewState
from butler.rsvp.controller import RsvpController
from butler.rsvp.modals.room_link import RoomLinkModal, parse_ping_mode
from butler.rsvp.view.event_message_view import (
    AnnounceRoomButton,
    DismissRoomSectionButton,
    EventMessageView,
    RoomItemActions,
)
from tests.discord_mocks import make_guild, make_interaction, make_member, make_text_channel

ROOM_URL = "https://botc.app/join/demo"


def _view(tmp_path: Path, *, state: str = "open") -> EventMessageView:
    store = RsvpMessageStore.load(tmp_path / "v.db")
    view_state = ViewState(
        event_name="Kväll",
        start_unix=1,
        event_url="https://example.com/event",
        edition=None,
        edition_emoji=None,
        edition_image_url=None,
        event_description="d",
        rooms=(RoomEntry(room_id="r1", url=ROOM_URL, state=cast(Any, state)),),
    )
    return EventMessageView(
        view_state=view_state,
        controller=RsvpController(store=store),
        responses={7: RsvpResponse(status="Available"), 8: RsvpResponse(status="Maybe")},
    )


def _room_row(view: EventMessageView) -> RoomItemActions:
    return next(item for item in view.walk_children() if isinstance(item, RoomItemActions))


def test_open_room_row_has_announce_next_to_close(tmp_path: Path) -> None:
    row = _room_row(_view(tmp_path))
    kinds = [type(child) for child in row.children]
    assert kinds == [AnnounceRoomButton, DismissRoomSectionButton]
    assert cast(Any, row.children[0]).custom_id == "butler:rsvp:announce-room:r1"


def test_closed_room_row_has_no_announce(tmp_path: Path) -> None:
    row = _room_row(_view(tmp_path, state="closed"))
    assert [type(child) for child in row.children] == [DismissRoomSectionButton]


def test_parse_ping_mode_defaults_to_rsvp() -> None:
    assert parse_ping_mode(["players"]) == "players"
    assert parse_ping_mode(["none"]) == "none"
    assert parse_ping_mode([]) == "rsvp"
    assert parse_ping_mode(["bogus"]) == "rsvp"


def test_modal_variants() -> None:
    open_modal = RoomLinkModal(on_submit=AsyncMock())
    assert open_modal.title == ROOM_LINK_MODAL_TITLE
    assert any(isinstance(item, ui.TextInput) and item.required for item in open_modal.children)

    announce = RoomLinkModal(on_submit=AsyncMock(), fixed_room_url=ROOM_URL)
    assert announce.title == ROOM_ANNOUNCE_MODAL_TITLE
    displays = [item for item in announce.children if isinstance(item, ui.TextDisplay)]
    assert len(displays) == 1 and ROOM_URL in displays[0].content
    # Only the optional message remains as a text input.
    inputs = [item for item in announce.children if isinstance(item, ui.TextInput)]
    assert len(inputs) == 1 and not inputs[0].required
    labels = [item for item in announce.children if isinstance(item, ui.Label)]
    select = cast(ui.Select[Any], labels[0].component)
    assert [o.value for o in select.options] == ["none", "rsvp", "players"]
    assert [o.value for o in select.options if o.default] == ["rsvp"]


async def test_announce_button_opens_fixed_link_modal(tmp_path: Path) -> None:
    view = _view(tmp_path)
    ix = make_interaction(guild=make_guild(), user=make_member())
    await view.open_announce_room_modal(ix.interaction, room_id="r1")
    ix.response.send_modal.assert_awaited_once()
    assert ix.response.send_modal.await_args is not None
    modal = ix.response.send_modal.await_args.args[0]
    assert isinstance(modal, RoomLinkModal)
    assert modal.title == ROOM_ANNOUNCE_MODAL_TITLE
    labels = [item for item in modal.children if isinstance(item, ui.Label)]
    select = cast(ui.Select[Any], labels[0].component)
    assert [o.value for o in select.options if o.default] == ["none"]


async def test_announce_rejects_room_no_longer_open(tmp_path: Path) -> None:
    view = _view(tmp_path, state="closed")
    ix = make_interaction(guild=make_guild(), user=make_member())
    await view.open_announce_room_modal(ix.interaction, room_id="r1")
    ix.response.send_modal.assert_not_awaited()
    assert ix.response.send_message.await_args is not None
    assert ix.response.send_message.await_args.args[0] == ROOM_ANNOUNCE_ROOM_GONE_MESSAGE


async def _submit(view: EventMessageView, mode: str, guild: discord.Guild) -> tuple[Any, Any]:
    channel = make_text_channel()
    send = AsyncMock()
    cast(Any, channel).send = send
    ix = make_interaction(guild=guild, user=make_member(), channel=channel)
    await view.submit_room_announce(
        ix.interaction,
        room_id="r1",
        ping_mode=cast(Any, mode),
        optional_message="Kom in!",
    )
    return ix, send


async def test_submit_announce_pings_rsvp(tmp_path: Path) -> None:
    _ix, send = await _submit(_view(tmp_path), "rsvp", make_guild())
    assert send.await_args is not None
    content = send.await_args.args[0]
    assert content.startswith("Hej <@7> <@8>!")
    assert ROOM_URL in content and content.endswith("Kom in!")


async def test_submit_announce_without_ping(tmp_path: Path) -> None:
    _ix, send = await _submit(_view(tmp_path), "none", make_guild())
    assert send.await_args is not None
    assert send.await_args.args[0].startswith("Hej!\n")


async def test_submit_announce_pings_player_role(tmp_path: Path) -> None:
    guild = make_guild()
    role = MagicMock(spec=discord.Role)
    role.id = 42
    role.name = "Spelare"
    cast(Any, guild).roles = [role]
    _ix, send = await _submit(_view(tmp_path), "players", guild)
    assert send.await_args is not None
    assert send.await_args.args[0].startswith("Hej <@&42>!")


async def test_submit_announce_warns_when_player_role_missing(tmp_path: Path) -> None:
    guild = make_guild()
    cast(Any, guild).roles = []
    ix, send = await _submit(_view(tmp_path), "players", guild)
    assert send.await_args is not None
    assert send.await_args.args[0].startswith("Hej!\n")
    followup = cast(Any, ix.interaction).followup.send
    followup.assert_awaited_once()
    assert followup.await_args.args[0] == PLAYER_ROLE_MISSING_MESSAGE
