from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from butler.domains.rsvp.domain import add_room
from butler.domains.rsvp.store import StoredRsvpMessage
from butler.domains.rsvp.types import RoomEntry, ViewState
from butler.rooms.types import BotcJoinSnapshot
from butler.rsvp.controller import RsvpController
from butler.rsvp.room_poll import RoomPollSummary, refresh_guild_botc_rooms
from butler.rsvp.view.event_message_view import EventMessageView


def _view_state(*, rooms: tuple[RoomEntry, ...] = ()) -> ViewState:
    base = ViewState(
        event_name="Test",
        start_unix=1,
        event_url="https://discord.com/channels/1/1",
        edition=None,
        edition_emoji=None,
        edition_image_url=None,
        event_description="desc",
    )
    return base.with_rooms(rooms) if rooms else base


@pytest.mark.asyncio
async def test_refresh_guild_botc_rooms_applies_snapshots_and_edits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    open_snap = BotcJoinSnapshot(
        code="kaonsdag",
        occupancy="open",
        is_private=False,
        open_seats=3,
        edition="Trouble Brewing",
        phase="Night",
        storytellers=(),
        players=(),
        player_count=0,
    )
    rooms = add_room((), url="https://botc.app/join/kaonsdag", botc=None, now_unix=10)
    assert len(rooms) == 1

    store = MagicMock()
    store.list_messages.return_value = [
        StoredRsvpMessage(
            message_id=100,
            channel_id=20,
            guild_id=5,
            view_state=_view_state(rooms=rooms),
        ),
        StoredRsvpMessage(
            message_id=101,
            channel_id=20,
            guild_id=99,  # other guild — ignored
            view_state=_view_state(rooms=rooms),
        ),
    ]
    store.all_responses.return_value = {}

    controller = RsvpController(store=store)
    view = EventMessageView(
        view_state=_view_state(rooms=rooms),
        controller=controller,
        message_id=100,
        channel_id=20,
        guild_id=5,
    )
    active: dict[int, EventMessageView] = {100: view}

    fetch = AsyncMock(return_value=open_snap)
    monkeypatch.setattr("butler.rsvp.room_poll.fetch_botc_join_snapshot", fetch)

    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 20

    bot = MagicMock()
    bot.get_channel.return_value = channel
    bot.add_view = MagicMock()

    edited: list[object] = []

    async def fake_edit(*, bot: object, view: EventMessageView) -> bool:
        edited.append(view)
        return True

    monkeypatch.setattr("butler.rsvp.room_poll._edit_rsvp_view", fake_edit)

    summary = await refresh_guild_botc_rooms(
        guild_id=5,
        bot=bot,
        view_store=store,
        controller=controller,
        active_views=active,
        settings_store=MagicMock(),
    )

    fetch.assert_awaited()
    assert summary.messages_seen == 1
    assert summary.messages_with_botc_rooms == 1
    assert summary.rooms_checked == 1
    assert summary.rooms_failed == 0
    assert summary.messages_edited == 1
    assert len(edited) == 1
    assert view.view_state.rooms[0].botc is not None
    assert view.view_state.rooms[0].botc.edition == "Trouble Brewing"
    assert view.view_state.room_state == "open"

    # Second poll with identical display content must fetch/persist but not edit.
    summary_again = await refresh_guild_botc_rooms(
        guild_id=5,
        bot=bot,
        view_store=store,
        controller=controller,
        active_views=active,
        settings_store=MagicMock(),
    )
    assert summary_again.rooms_checked == 1
    assert summary_again.messages_edited == 0
    assert len(edited) == 1


@pytest.mark.asyncio
async def test_refresh_skips_messages_without_botc_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rooms = add_room((), url="https://example.com/room", botc=None, now_unix=10)
    store = MagicMock()
    store.list_messages.return_value = [
        StoredRsvpMessage(
            message_id=1,
            channel_id=2,
            guild_id=5,
            view_state=_view_state(rooms=rooms),
        )
    ]
    store.all_responses.return_value = {}
    controller = RsvpController(store=store)
    view = EventMessageView(
        view_state=_view_state(rooms=rooms),
        controller=controller,
        message_id=1,
        channel_id=2,
        guild_id=5,
    )
    fetch = AsyncMock()
    monkeypatch.setattr("butler.rsvp.room_poll.fetch_botc_join_snapshot", fetch)

    summary = await refresh_guild_botc_rooms(
        guild_id=5,
        bot=MagicMock(),
        view_store=store,
        controller=controller,
        active_views={1: view},
        settings_store=MagicMock(),
    )
    fetch.assert_not_awaited()
    assert summary == RoomPollSummary(
        messages_seen=1,
        messages_with_botc_rooms=0,
        rooms_checked=0,
        rooms_failed=0,
        messages_edited=0,
        newly_opened=0,
        elapsed_ms=summary.elapsed_ms,
    )
