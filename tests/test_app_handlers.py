"""Unit tests for the command and interaction handlers in `butler.app`.

These exercise the imperative shell's branching: guard clauses, permission gating, error
surfacing, and the happy paths. Discord objects are spec'd mocks (so `isinstance` guards
pass) with `AsyncMock` surfaces we assert on. Module globals (`SETTINGS_STORE`,
`get_bot_member`, `ACTIVE_RSVP_VIEWS`, `bot`) are patched per test.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

import butler.app as app
import butler.bot_events as bot_events
import butler.rsvp2.event_command as event_command
import butler.rsvp2.runtime as rsvp_runtime
from butler.caches.events import reset_connected_event_cache
from butler.domains.rsvp.store import StoredRsvpMessage
from butler.domains.rsvp.types import ViewState
from tests.discord_mocks import (
    invoke,
    make_guild,
    make_interaction,
    make_member,
    make_message,
    make_permissions,
    make_role,
    make_scheduled_event,
    make_text_channel,
    sent_text,
)


@pytest.fixture(autouse=True)
def store() -> Iterator[MagicMock]:
    """Patch the module-level settings store with a mock and yield it."""
    mock = MagicMock()
    mock.get_event_manager_role_id.return_value = None
    mock.get_default_event_channel_id.return_value = 10
    rsvp_store_mock = MagicMock()
    rsvp_store_mock.get_message.return_value = None
    rsvp_store_mock.list_messages.return_value = []
    original = app.SETTINGS_STORE
    original_rsvp_store = app.RSVP_MESSAGE_STORE
    app.SETTINGS_STORE = mock
    app.RSVP_MESSAGE_STORE = rsvp_store_mock
    try:
        yield mock
    finally:
        app.SETTINGS_STORE = original
        app.RSVP_MESSAGE_STORE = original_rsvp_store

@pytest.fixture(autouse=True)
def event_cache() -> Iterator[None]:
    reset_connected_event_cache()
    try:
        yield
    finally:
        reset_connected_event_cache()


@pytest.fixture
def views() -> Iterator[dict[int, object]]:
    """Give the handler a fresh ACTIVE_RSVP_VIEWS registry."""
    fresh: dict[int, object] = {}
    original = app.ACTIVE_RSVP_VIEWS
    original_hydrated = app._BOT_EVENT_STATE.rsvp_views_hydrated
    app.ACTIVE_RSVP_VIEWS = fresh  # type: ignore[assignment]
    app._BOT_EVENT_STATE.rsvp_views_hydrated = False
    try:
        yield fresh
    finally:
        app.ACTIVE_RSVP_VIEWS = original
        app._BOT_EVENT_STATE.rsvp_views_hydrated = original_hydrated


@pytest.fixture
def bot_member_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch get_bot_member to return a fully-permissioned bot member."""
    monkeypatch.setattr(
        app,
        "get_bot_member",
        lambda guild, user: make_member(member_id=2))  # pyright: ignore[reportUnknownLambdaType, reportUnknownArgumentType]


def _reusable_today_event(*, event_id: int, name: str) -> discord.ScheduledEvent:
    return make_scheduled_event(
        event_id=event_id,
        name=name,
        status=discord.EventStatus.active,
        start_time=dt.datetime.now(dt.UTC),
    )


# --- /seteventchannel -------------------------------------------------------


async def test_seteventchannel_requires_guild() -> None:
    ix = make_interaction(guild=None)
    await invoke(app.seteventchannel, ix.interaction, make_text_channel())
    assert "must be used in a server" in sent_text(ix.response.send_message)


async def test_seteventchannel_rejects_cross_guild_channel() -> None:
    ix = make_interaction(guild=make_guild(guild_id=1))
    foreign_channel = make_text_channel(guild_id=2)
    await invoke(app.seteventchannel, ix.interaction, foreign_channel)
    assert "from this server" in sent_text(ix.response.send_message)


async def test_seteventchannel_missing_bot_member(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app, "get_bot_member", lambda guild, user: None) # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
    ix = make_interaction(guild=make_guild(guild_id=1))
    await invoke(app.seteventchannel, ix.interaction, make_text_channel(guild_id=1))
    assert "couldn't verify my server permissions" in sent_text(ix.response.send_message)


async def test_seteventchannel_missing_post_permissions(bot_member_ok: None) -> None:
    ix = make_interaction(guild=make_guild(guild_id=1))
    channel = make_text_channel(guild_id=1, permissions=make_permissions(send_messages=False))
    await invoke(app.seteventchannel, ix.interaction, channel)
    assert "Missing permissions" in sent_text(ix.response.send_message)


async def test_seteventchannel_save_failure(store: MagicMock, bot_member_ok: None) -> None:
    store.set_default_event_channel_id.side_effect = OSError("disk")
    ix = make_interaction(guild=make_guild(guild_id=1))
    await invoke(app.seteventchannel, ix.interaction, make_text_channel(guild_id=1))
    assert "Failed to save" in sent_text(ix.response.send_message)


async def test_seteventchannel_success(store: MagicMock, bot_member_ok: None) -> None:
    ix = make_interaction(guild=make_guild(guild_id=1))
    channel = make_text_channel(channel_id=77, guild_id=1)
    await invoke(app.seteventchannel, ix.interaction, channel)
    store.set_default_event_channel_id.assert_called_once_with(1, 77)
    assert "Default event channel set" in sent_text(ix.response.send_message)


# --- /seteventrole ----------------------------------------------------------


async def test_seteventrole_requires_guild() -> None:
    ix = make_interaction(guild=None)
    await invoke(app.seteventrole, ix.interaction, make_role(role_id=5))
    assert "must be used in a server" in sent_text(ix.response.send_message)


async def test_seteventrole_rejects_cross_guild_role() -> None:
    ix = make_interaction(guild=make_guild(guild_id=1))
    role = make_role(role_id=5)
    role.guild.id = 2
    await invoke(app.seteventrole, ix.interaction, role)
    assert "from this server" in sent_text(ix.response.send_message)


async def test_seteventrole_set_success(store: MagicMock) -> None:
    ix = make_interaction(guild=make_guild(guild_id=1))
    await invoke(app.seteventrole, ix.interaction, make_role(role_id=5))
    store.set_event_manager_role_id.assert_called_once_with(1, 5)
    assert "can now create events" in sent_text(ix.response.send_message)


async def test_seteventrole_clear_success(store: MagicMock) -> None:
    ix = make_interaction(guild=make_guild(guild_id=1))
    await invoke(app.seteventrole, ix.interaction, None)
    store.clear_event_manager_role_id.assert_called_once_with(1)
    assert "Cleared the event manager role" in sent_text(ix.response.send_message)


async def test_seteventrole_save_failure(store: MagicMock) -> None:
    store.set_event_manager_role_id.side_effect = OSError("disk")
    ix = make_interaction(guild=make_guild(guild_id=1))
    await invoke(app.seteventrole, ix.interaction, make_role(role_id=5))
    assert "Failed to save" in sent_text(ix.response.send_message)


# --- /event -----------------------------------------------------------------


async def test_event_requires_guild() -> None:
    ix = make_interaction(guild=None, user=make_member())
    await invoke(
        app.event,
        ix.interaction,
        title="t",
        description="d",
        event=event_command.CREATE_NEW_EVENT_CHOICE_VALUE,
    )
    assert "must be used in a server" in sent_text(ix.response.send_message)


async def test_event_requires_member(monkeypatch: pytest.MonkeyPatch) -> None:
    # A plain user (not a Member) must fail the isinstance guard.
    non_member = MagicMock(spec=__import__("discord").User)
    ix = make_interaction(guild=make_guild(guild_id=1), user=non_member)
    await invoke(
        app.event,
        ix.interaction,
        title="t",
        description="d",
        event=event_command.CREATE_NEW_EVENT_CHOICE_VALUE,
    )
    assert "verify your server member permissions" in sent_text(ix.response.send_message)


async def test_event_permission_denied(store: MagicMock) -> None:
    # No manage_guild and no configured role -> denied.
    ix = make_interaction(
        guild=make_guild(guild_id=1),
        user=make_member(permissions=make_permissions(manage_guild=False)),
    )
    await invoke(
        app.event,
        ix.interaction,
        title="t",
        description="d",
        event=event_command.CREATE_NEW_EVENT_CHOICE_VALUE,
    )
    assert "behörigheten" in sent_text(ix.response.send_message)


async def test_event_no_channel_configured(store: MagicMock) -> None:
    store.get_default_event_channel_id.return_value = None
    ix = make_interaction(guild=make_guild(guild_id=1), user=make_member())
    await invoke(
        app.event,
        ix.interaction,
        title="t",
        description="d",
        event=event_command.CREATE_NEW_EVENT_CHOICE_VALUE,
    )
    assert "No valid default event channel" in sent_text(ix.followup.send)


async def test_event_invalid_room_link_surfaces_error(store: MagicMock) -> None:
    channel = make_text_channel(channel_id=10, guild_id=1)
    ix = make_interaction(
        guild=make_guild(guild_id=1, channel=channel), user=make_member()
    )
    await invoke(
        app.event,
        ix.interaction,
        title="t",
        description="d",
        event=event_command.CREATE_NEW_EVENT_CHOICE_VALUE,
        room_link="not-a-url",
    )
    assert "must be a full URL" in sent_text(ix.followup.send)


async def test_event_missing_bot_permissions(
    store: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Bot member lacks event permissions -> creation blocked with details.
    monkeypatch.setattr(
        app,
        "get_bot_member",
        lambda guild, user: make_member(permissions=make_permissions(create_events=False)), # pyright: ignore[reportUnknownLambdaType, reportUnknownArgumentType]
    )
    channel = make_text_channel(channel_id=10, guild_id=1)
    ix = make_interaction(guild=make_guild(guild_id=1, channel=channel), user=make_member())
    await invoke(
        app.event,
        ix.interaction,
        title="t",
        description="d",
        event=event_command.CREATE_NEW_EVENT_CHOICE_VALUE,
    )
    assert "required permissions" in sent_text(ix.followup.send)


async def test_event_success_registers_view(
    store: MagicMock, views: dict[int, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app, "get_bot_member", lambda guild, user: make_member(member_id=2)) # pyright: ignore[reportUnknownLambdaType, reportUnknownArgumentType]
    channel = make_text_channel(channel_id=10, guild_id=1)
    guild = make_guild(guild_id=1, channel=channel)
    ix = make_interaction(guild=guild, user=make_member())
    await invoke(
        app.event,
        ix.interaction,
        title="Game",
        description="d",
        event=event_command.CREATE_NEW_EVENT_CHOICE_VALUE,
        start_time="23:59",
    )
    cast(Any, guild).create_scheduled_event.assert_awaited_once()
    # The posted RSVP message (id 999 from make_message) is tracked.
    assert 999 in views
    assert "Created" in sent_text(ix.followup.send)

async def test_event_existing_event_autocomplete_filters_and_caps() -> None:
    now_utc = dt.datetime.now(dt.UTC)
    scheduled_events = [
        _reusable_today_event(event_id=index, name=f"Game {index}")
        for index in range(1, 30)
    ] + [
        make_scheduled_event(
            event_id=5000,
            name="Old Game",
            status=discord.EventStatus.completed,
        ),
        make_scheduled_event(
            event_id=6000,
            name="Tomorrow Game",
            start_time=now_utc + dt.timedelta(days=1),
        ),
    ]
    guild = make_guild(guild_id=1, scheduled_events=scheduled_events)
    await event_command.warmup_connected_event_cache(guilds=[guild])
    ix = make_interaction(guild=guild, user=make_member())

    choices = await event_command.autocomplete_existing_event(
        ix.interaction,
        "game",
    )

    assert len(choices) == 25
    assert choices[0].name == event_command.CREATE_NEW_EVENT_CHOICE_LABEL
    assert choices[0].value == event_command.CREATE_NEW_EVENT_CHOICE_VALUE
    assert all("Game" in choice.name for choice in choices[1:])
    assert all("svensk tid" not in choice.name for choice in choices[1:])
    assert all(choice.value not in {"5000", "6000"} for choice in choices)
    for choice in choices[1:]:
        name_parts = choice.name.rsplit(" — ", 1)
        assert len(name_parts) == 2
        assert len(name_parts[1]) == 5
        assert name_parts[1][2] == ":"

async def test_event_existing_event_autocomplete_does_not_fetch_api_per_keystroke() -> None:
    reset_connected_event_cache()
    guild = make_guild(
        guild_id=1,
        scheduled_events=[make_scheduled_event(event_id=1, name="Game 1")],
    )
    # Warm once (this may hit Discord APIs), then keystrokes should reuse cache.
    await event_command.warmup_connected_event_cache(guilds=[guild])
    cast(Any, guild).fetch_scheduled_events.reset_mock()
    ix = make_interaction(guild=guild, user=make_member())

    choices = await event_command.autocomplete_existing_event(
        ix.interaction,
        "game",
    )
    assert choices[0].name == event_command.CREATE_NEW_EVENT_CHOICE_LABEL
    assert choices[0].value == event_command.CREATE_NEW_EVENT_CHOICE_VALUE
    # Create-new choice only when the warmed event is not reusable for "today".
    # After a successful warm of a reusable event, expect create-new + that event.
    assert len(choices) >= 1
    cast(Any, guild).fetch_scheduled_events.assert_not_awaited()

def test_event_choice_label_uses_swedish_summer_time() -> None:
    event = make_scheduled_event(
        event_id=7000,
        name="Summer Game",
        start_time=dt.datetime(2026, 7, 14, 17, 0, tzinfo=dt.UTC),
    )

    from butler.caches.events import event_choice_name

    choice_name = event_choice_name(event)

    assert choice_name == "Summer Game — 19:00"


async def test_event_reuses_selected_existing_event(
    views: dict[int, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        app,
        "get_bot_member",
        lambda guild, user: make_member(member_id=2),  # pyright: ignore[reportUnknownLambdaType, reportUnknownArgumentType]
    )
    channel = make_text_channel(channel_id=10, guild_id=1)
    selected_event = _reusable_today_event(event_id=321, name="Existing Event")
    guild = make_guild(
        guild_id=1,
        channel=channel,
        scheduled_events=[selected_event],
    )
    ix = make_interaction(guild=guild, user=make_member())

    await invoke(
        app.event,
        ix.interaction,
        title="Game",
        description="d",
        event="321",
    )
    cast(Any, guild).create_scheduled_event.assert_not_called()
    assert 999 in views
    assert "Posted RSVP for **Existing Event**" in sent_text(ix.followup.send)


async def test_event_selected_existing_event_stale_returns_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        app,
        "get_bot_member",
        lambda guild, user: make_member(member_id=2),  # pyright: ignore[reportUnknownLambdaType, reportUnknownArgumentType]
    )
    channel = make_text_channel(channel_id=10, guild_id=1)
    guild = make_guild(guild_id=1, channel=channel, scheduled_events=[])
    ix = make_interaction(guild=guild, user=make_member())

    await invoke(
        app.event,
        ix.interaction,
        title="Game",
        description="d",
        event="321",
    )
    cast(Any, guild).create_scheduled_event.assert_not_called()
    assert "couldn't find that selected event" in sent_text(ix.followup.send)


async def test_event_create_option_uses_old_create_behavior(
    views: dict[int, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        app,
        "get_bot_member",
        lambda guild, user: make_member(member_id=2),  # pyright: ignore[reportUnknownLambdaType, reportUnknownArgumentType]
    )
    channel = make_text_channel(channel_id=10, guild_id=1)
    reusable_event = make_scheduled_event(event_id=777, name="Lookup Event")
    guild = make_guild(
        guild_id=1,
        channel=channel,
        scheduled_events=[reusable_event],
    )
    ix = make_interaction(guild=guild, user=make_member())
    await invoke(
        app.event,
        ix.interaction,
        title="Game",
        description="d",
        event=event_command.CREATE_NEW_EVENT_CHOICE_VALUE,
    )
    cast(Any, guild).create_scheduled_event.assert_awaited_once()
    assert 999 in views
    assert "Created" in sent_text(ix.followup.send)


async def test_on_ready_warms_cache_before_command_sync(
    store: MagicMock,
    views: dict[int, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = store
    _ = views
    hydrate = AsyncMock(return_value=True)
    monkeypatch.setattr(rsvp_runtime, "hydrate_persistent_views", hydrate)
    warm_calls: list[bool] = []
    sync_calls: list[str] = []

    async def _fake_warmup(*, guilds: list[object], force: bool = False) -> None:
        warm_calls.append(force)
        from butler.caches import events as events_cache

        if guilds:
            guild0 = cast(Any, guilds[0])
            events_cache.cache_reusable_events_for_guild(
                guild_id=guild0.id,
                events=list(guild0.scheduled_events or []),
            )

    async def _fake_sync(*, deps: object) -> None:
        _ = deps
        sync_calls.append("synced")
        assert warm_calls, "command sync must run after cache warm"

    monkeypatch.setattr(bot_events, "warmup_connected_event_cache", _fake_warmup)
    monkeypatch.setattr(bot_events, "_sync_commands_on_startup", _fake_sync)

    warm_event = _reusable_today_event(event_id=111, name="Warm Event")
    bot = MagicMock()
    bot.user = MagicMock()
    bot.user.id = 1
    bot.wait_until_ready = AsyncMock()
    bot.tree = MagicMock()
    bot.guilds = [
        make_guild(guild_id=1, scheduled_events=[warm_event]),
        make_guild(guild_id=2, scheduled_events=[]),
    ]
    monkeypatch.setattr(app, "bot", bot)

    app._BOT_EVENT_STATE.rsvp_views_hydrated = False
    app._BOT_EVENT_STATE.event_cache_boot_hydrated = False
    app._BOT_EVENT_STATE.commands_synced = False
    app._BOT_EVENT_STATE.event_cache_daily_sync_started = False
    daily_sync = app._REGISTERED_BOT_EVENTS.daily_event_cache_sync
    if daily_sync.is_running():
        daily_sync.cancel()
    try:
        await app.on_ready()
        assert warm_calls == [True]
        assert sync_calls == ["synced"]
        hydrate.assert_awaited()
        assert app._BOT_EVENT_STATE.rsvp_views_hydrated is True
        assert app._BOT_EVENT_STATE.event_cache_boot_hydrated is True
        assert app._BOT_EVENT_STATE.commands_synced is True
        assert app._BOT_EVENT_STATE.event_cache_daily_sync_started is True
        assert event_command.cached_connected_event_id(guild_id=1) == 111
    finally:
        if daily_sync.is_running():
            daily_sync.cancel()
        app._BOT_EVENT_STATE.event_cache_daily_sync_started = False
        app._BOT_EVENT_STATE.event_cache_boot_hydrated = False
        app._BOT_EVENT_STATE.commands_synced = False

# --- hydrate / resolve ------------------------------------------------------


@pytest.fixture
def reaction_bot(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Patch app.bot with a mock exposing user + a channel that returns a message."""
    message = make_message(message_id=999)
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=message)
    bot = MagicMock()
    bot.user = MagicMock()
    bot.user.id = 1
    bot.get_channel = MagicMock(return_value=None)
    bot.fetch_channel = AsyncMock(return_value=channel)
    monkeypatch.setattr(app, "bot", bot)
    return bot


def _stored_message(
    *,
    message_id: int = 999,
    channel_id: int = 10,
    guild_id: int = 1,
) -> StoredRsvpMessage:
    return StoredRsvpMessage(
        message_id=message_id,
        channel_id=channel_id,
        guild_id=guild_id,
        view_state=ViewState(
            event_name="Game Night",
            start_unix=1_700_000_000,
            event_url="https://discord.com/events/1/2",
            edition="Custom",
            edition_emoji=None,
            room_state="pending",
            room_url=None,
            edition_image_url=None,
            event_description="desc",
        ),
    )


def _text_channel_with_message(message: object) -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.fetch_message = AsyncMock(return_value=message)
    return channel


async def test_resolve_active_view_rehydrates_from_store(
    reaction_bot: MagicMock,
    store: MagicMock,
    views: dict[int, object],
) -> None:
    _ = store
    rsvp_store = cast(MagicMock, app.RSVP_MESSAGE_STORE)
    rsvp_store.get_message.return_value = _stored_message(message_id=999, channel_id=10)
    rsvp_store.all_responses.return_value = {}
    reaction_bot.fetch_channel.return_value = _text_channel_with_message(
        make_message(message_id=999),
    )

    resolved = await rsvp_runtime.resolve_active_view(
        message_id=999,
        channel_id=10,
        active_views=app.ACTIVE_RSVP_VIEWS,
        bot=app.bot,
        settings_store=app.SETTINGS_STORE,
        view_store=app.RSVP_MESSAGE_STORE,
        controller=app.RSVP_CONTROLLER,
    )

    assert resolved is not None
    assert 999 in views
    reaction_bot.add_view.assert_called()


async def test_resolve_active_view_cleans_stale_store_entry(
    reaction_bot: MagicMock,
    store: MagicMock,
    views: dict[int, object],
) -> None:
    _ = store
    _ = views
    rsvp_store = cast(MagicMock, app.RSVP_MESSAGE_STORE)
    rsvp_store.get_message.return_value = _stored_message(message_id=999, channel_id=10)
    reaction_bot.fetch_channel.return_value = MagicMock()  # Not a TextChannel/Thread.

    resolved = await rsvp_runtime.resolve_active_view(
        message_id=999,
        channel_id=10,
        active_views=app.ACTIVE_RSVP_VIEWS,
        bot=app.bot,
        settings_store=app.SETTINGS_STORE,
        view_store=app.RSVP_MESSAGE_STORE,
        controller=app.RSVP_CONTROLLER,
    )

    assert resolved is None
    rsvp_store.delete_message.assert_called_once_with(999)


async def test_hydrate_persistent_views_registers_without_fetch(
    reaction_bot: MagicMock,
    store: MagicMock,
    views: dict[int, object],
) -> None:
    _ = store
    _ = reaction_bot
    rsvp_store = cast(MagicMock, app.RSVP_MESSAGE_STORE)
    rsvp_store.list_messages.return_value = [
        _stored_message(message_id=999, channel_id=10),
        _stored_message(message_id=1000, channel_id=11),
    ]
    rsvp_store.all_responses.return_value = {}

    app._BOT_EVENT_STATE.rsvp_views_hydrated = await rsvp_runtime.hydrate_persistent_views(
        already_hydrated=app._BOT_EVENT_STATE.rsvp_views_hydrated,
        active_views=app.ACTIVE_RSVP_VIEWS,
        bot=app.bot,
        settings_store=app.SETTINGS_STORE,
        view_store=app.RSVP_MESSAGE_STORE,
        controller=app.RSVP_CONTROLLER,
    )

    assert 999 in views
    assert 1000 in views
    assert app._BOT_EVENT_STATE.rsvp_views_hydrated is True
