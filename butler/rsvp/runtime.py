from __future__ import annotations

import logging
from collections.abc import MutableMapping

import discord
from discord.ext import commands

from butler.discord_helpers import fetch_message_from_channel
from butler.domains.rsvp.store import RsvpMessageStore, StoredRsvpMessage
from butler.rsvp.controller import RsvpController
from butler.rsvp.view.event_message_view import EventMessageView
from butler.settings_store import GuildSettingsStore

logger = logging.getLogger(__name__)


def _view_from_stored(
    *,
    stored: StoredRsvpMessage,
    controller: RsvpController,
    settings_store: GuildSettingsStore,
    view_store: RsvpMessageStore,
) -> EventMessageView:
    try:
        responses = view_store.all_responses(stored.message_id)
    except OSError:
        logger.exception(
            "Failed to load RSVP responses for message %s; hydrating empty",
            stored.message_id,
        )
        responses = {}
    return EventMessageView(
        view_state=stored.view_state,
        controller=controller,
        settings_store=settings_store,
        message_id=stored.message_id,
        channel_id=stored.channel_id,
        guild_id=stored.guild_id,
        responses=responses,
    )


def register_view(
    *,
    view: EventMessageView,
    active_views: MutableMapping[int, EventMessageView],
    bot: commands.Bot,
) -> None:
    message_id = view.message_id
    if message_id is None:
        return
    active_views[message_id] = view
    try:
        bot.add_view(view, message_id=message_id)
    except ValueError:
        logger.exception("Failed to register persistent view for message %s", message_id)


def drop_view(
    *,
    message_id: int,
    active_views: MutableMapping[int, EventMessageView],
) -> None:
    active_views.pop(message_id, None)


def delete_stored_message(*, message_id: int, view_store: RsvpMessageStore) -> None:
    try:
        view_store.delete_message(message_id)
    except OSError:
        logger.exception("Failed to delete stale RSVP state for %s", message_id)


def bind_and_register_posted_view(
    *,
    view: EventMessageView,
    message: discord.Message,
    channel_id: int,
    guild_id: int,
    active_views: MutableMapping[int, EventMessageView],
    bot: commands.Bot,
) -> str | None:
    warning: str | None = None
    try:
        view.bind_message_context(
            message_id=message.id,
            channel_id=channel_id,
            guild_id=guild_id,
        )
    except OSError as exc:
        warning = (
            "Posted RSVP message, but failed to persist RSVP state. "
            "Restart recovery may not work until storage errors are fixed."
        )
        logger.exception("Failed to persist RSVP state for message %s: %s", message.id, exc)
    register_view(view=view, active_views=active_views, bot=bot)
    return warning


async def resolve_active_view(
    *,
    message_id: int,
    channel_id: int,
    active_views: MutableMapping[int, EventMessageView],
    bot: commands.Bot,
    settings_store: GuildSettingsStore,
    view_store: RsvpMessageStore,
    controller: RsvpController,
) -> EventMessageView | None:
    """Lazy-load a view from the store when missing from the in-memory index."""
    cached = active_views.get(message_id)
    if cached is not None:
        return cached
    try:
        stored = view_store.get_message(message_id)
    except OSError:
        logger.exception("Failed to read RSVP state for %s", message_id)
        return None
    if stored is None:
        return None
    channel_candidates = [channel_id]
    if stored.channel_id != channel_id:
        channel_candidates.append(stored.channel_id)
    message: discord.Message | None = None
    for candidate_channel_id in channel_candidates:
        message = await fetch_message_from_channel(
            bot=bot,
            channel_id=candidate_channel_id,
            message_id=message_id,
        )
        if message is not None:
            break
    if message is None:
        delete_stored_message(message_id=message_id, view_store=view_store)
        drop_view(message_id=message_id, active_views=active_views)
        return None
    view = _view_from_stored(
        stored=stored,
        controller=controller,
        settings_store=settings_store,
        view_store=view_store,
    )
    register_view(view=view, active_views=active_views, bot=bot)
    return view


async def hydrate_persistent_views(
    *,
    already_hydrated: bool,
    active_views: MutableMapping[int, EventMessageView],
    bot: commands.Bot,
    settings_store: GuildSettingsStore,
    view_store: RsvpMessageStore,
    controller: RsvpController,
) -> bool:
    """Register persistent LayoutViews from SQLite so buttons work after restart.

    Does not fetch Discord messages at boot. Orphan cleanup is lazy via
    ``resolve_active_view`` when a path proves the message is gone.
    """
    if already_hydrated:
        return True
    try:
        stored_messages = view_store.list_messages()
    except OSError:
        logger.exception("Failed to load persisted RSVP messages")
        return True
    hydrated = 0
    for stored in stored_messages:
        register_view(
            view=_view_from_stored(
                stored=stored,
                controller=controller,
                settings_store=settings_store,
                view_store=view_store,
            ),
            active_views=active_views,
            bot=bot,
        )
        hydrated += 1
    logger.info("RSVP hydration complete: restored=%s.", hydrated)
    return True
