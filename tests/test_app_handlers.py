"""Unit tests for the command and interaction handlers in `butler.app`.

These exercise the imperative shell's branching: guard clauses, permission gating, error
surfacing, and the happy paths. Discord objects are spec'd mocks (so `isinstance` guards
pass) with `AsyncMock` surfaces we assert on. Module globals (`SETTINGS_STORE`,
`get_bot_member`, `ACTIVE_RSVP_VIEWS`, `bot`) are patched per test.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

import butler.app as app
from butler.design import AVAILABLE_EMOJI, CANT_EMOJI
from tests.discord_mocks import (
    invoke,
    make_guild,
    make_interaction,
    make_member,
    make_message,
    make_permissions,
    make_raw_reaction,
    make_reaction,
    make_role,
    make_text_channel,
    sent_text,
)


@pytest.fixture
def store() -> Iterator[MagicMock]:
    """Patch the module-level settings store with a mock and yield it."""
    mock = MagicMock()
    mock.get_event_manager_role_id.return_value = None
    mock.get_default_event_channel_id.return_value = 10
    original = app.SETTINGS_STORE
    app.SETTINGS_STORE = mock
    try:
        yield mock
    finally:
        app.SETTINGS_STORE = original


@pytest.fixture
def views() -> Iterator[dict[int, object]]:
    """Give the handler a fresh ACTIVE_RSVP_VIEWS registry."""
    fresh: dict[int, object] = {}
    original = app.ACTIVE_RSVP_VIEWS
    app.ACTIVE_RSVP_VIEWS = fresh  # type: ignore[assignment]
    try:
        yield fresh
    finally:
        app.ACTIVE_RSVP_VIEWS = original


@pytest.fixture
def bot_member_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch get_bot_member to return a fully-permissioned bot member."""
    monkeypatch.setattr(app, "get_bot_member", lambda guild, user: make_member(member_id=2))


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
    monkeypatch.setattr(app, "get_bot_member", lambda guild, user: None)
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
    await invoke(app.event, ix.interaction, title="t", description="d")
    assert "must be used in a server" in sent_text(ix.response.send_message)


async def test_event_requires_member(monkeypatch: pytest.MonkeyPatch) -> None:
    # A plain user (not a Member) must fail the isinstance guard.
    non_member = MagicMock(spec=__import__("discord").User)
    ix = make_interaction(guild=make_guild(guild_id=1), user=non_member)
    await invoke(app.event, ix.interaction, title="t", description="d")
    assert "verify your server member permissions" in sent_text(ix.response.send_message)


async def test_event_permission_denied(store: MagicMock) -> None:
    # No manage_guild and no configured role -> denied.
    ix = make_interaction(
        guild=make_guild(guild_id=1),
        user=make_member(permissions=make_permissions(manage_guild=False)),
    )
    await invoke(app.event, ix.interaction, title="t", description="d")
    assert "behörigheten" in sent_text(ix.response.send_message)


async def test_event_no_channel_configured(store: MagicMock) -> None:
    store.get_default_event_channel_id.return_value = None
    ix = make_interaction(guild=make_guild(guild_id=1), user=make_member())
    await invoke(app.event, ix.interaction, title="t", description="d")
    assert "No valid default event channel" in sent_text(ix.followup.send)


async def test_event_invalid_room_link_surfaces_error(store: MagicMock) -> None:
    channel = make_text_channel(channel_id=10, guild_id=1)
    ix = make_interaction(
        guild=make_guild(guild_id=1, channel=channel), user=make_member()
    )
    await invoke(app.event, ix.interaction, title="t", description="d", room_link="not-a-url")
    assert "must be a full URL" in sent_text(ix.followup.send)


async def test_event_missing_bot_permissions(
    store: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Bot member lacks event permissions -> creation blocked with details.
    monkeypatch.setattr(
        app,
        "get_bot_member",
        lambda guild, user: make_member(permissions=make_permissions(create_events=False)),
    )
    channel = make_text_channel(channel_id=10, guild_id=1)
    ix = make_interaction(guild=make_guild(guild_id=1, channel=channel), user=make_member())
    await invoke(app.event, ix.interaction, title="t", description="d")
    assert "required permissions" in sent_text(ix.followup.send)


async def test_event_success_registers_view(
    store: MagicMock, views: dict[int, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app, "get_bot_member", lambda guild, user: make_member(member_id=2))
    channel = make_text_channel(channel_id=10, guild_id=1)
    guild = make_guild(guild_id=1, channel=channel)
    ix = make_interaction(guild=guild, user=make_member())
    await invoke(app.event, ix.interaction, title="Game", description="d", start_time="23:59")
    cast(Any, guild).create_scheduled_event.assert_awaited_once()
    # The posted RSVP message (id 999 from make_message) is tracked.
    assert 999 in views
    assert "Created" in sent_text(ix.followup.send)


# --- /previeweventdesign ----------------------------------------------------


async def test_previeweventdesign_requires_guild() -> None:
    ix = make_interaction(guild=None)
    await invoke(app.previeweventdesign, ix.interaction)
    assert "must be used in a server" in sent_text(ix.response.send_message)


async def test_previeweventdesign_success_posts_without_event(
    store: MagicMock, views: dict[int, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app, "get_bot_member", lambda guild, user: make_member(member_id=2))
    channel = make_text_channel(channel_id=10, guild_id=1)
    guild = make_guild(guild_id=1, channel=channel)
    ix = make_interaction(guild=guild, user=make_member())
    await invoke(app.previeweventdesign, ix.interaction)  # defaults for title/description
    cast(Any, guild).create_scheduled_event.assert_not_called()
    assert 999 in views
    assert "Posted design preview" in sent_text(ix.followup.send)


# --- on_raw_reaction_add / remove -------------------------------------------


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


def _reaction_view() -> MagicMock:
    view = MagicMock()
    view.set_user_response = AsyncMock()
    view.remove_user_response = AsyncMock()
    view.build_content = MagicMock(return_value="content")
    view.build_embed = MagicMock(return_value=None)
    return view


async def test_reaction_add_ignores_bot_own(reaction_bot: MagicMock) -> None:
    payload = make_raw_reaction(user_id=1, message_id=999, emoji=AVAILABLE_EMOJI)
    # No view registered; should simply no-op without error.
    await app.on_raw_reaction_add(payload)


async def test_reaction_add_unknown_message_noops(
    reaction_bot: MagicMock, views: dict[int, object]
) -> None:
    payload = make_raw_reaction(user_id=2, message_id=12345, emoji=AVAILABLE_EMOJI)
    await app.on_raw_reaction_add(payload)  # message id not in views -> no-op


async def test_reaction_add_records_status(
    reaction_bot: MagicMock, views: dict[int, object]
) -> None:
    view = _reaction_view()
    views[999] = view
    payload = make_raw_reaction(user_id=2, message_id=999, emoji=AVAILABLE_EMOJI)

    # fetch_message_from_channel needs a TextChannel; route through fetch_channel.
    reaction_bot.fetch_channel.return_value = _text_channel_with_message(
        make_message(message_id=999)
    )

    await app.on_raw_reaction_add(payload)
    view.set_user_response.assert_awaited_once_with(user_id=2, status="Available")


async def test_reaction_remove_resolves_status(
    reaction_bot: MagicMock, views: dict[int, object]
) -> None:
    view = _reaction_view()
    views[999] = view
    message = make_message(message_id=999)
    message.reactions = [make_reaction(emoji=CANT_EMOJI, user_ids=(2,))]
    reaction_bot.fetch_channel.return_value = _text_channel_with_message(message)
    payload = make_raw_reaction(user_id=2, message_id=999, emoji=CANT_EMOJI)

    await app.on_raw_reaction_remove(payload)
    view.set_user_response.assert_awaited_once_with(user_id=2, status="Cant")


# async def test_reaction_remove_clears_when_no_reactions(
#     reaction_bot: MagicMock, views: dict[int, object]
# ) -> None:
#     view = _reaction_view()
#     views[999] = view
#     message = make_message(message_id=999)
#     message.reactions = []
#     reaction_bot.fetch_channel.return_value = _text_channel_with_message(message)
#     payload = make_raw_reaction(user_id=2, message_id=999, emoji=LATER_EMOJI)

#     await app.on_raw_reaction_remove(payload)
#     view.remove_user_response.assert_awaited_once_with(2)


def _text_channel_with_message(message: object) -> object:
    import discord

    channel = MagicMock(spec=discord.TextChannel)
    channel.fetch_message = AsyncMock(return_value=message)
    return channel
