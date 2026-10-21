from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import MutableMapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime

import discord
from discord.ext import commands

from butler.discord_helpers import fetch_message_from_channel
from butler.domains.rsvp.store import RsvpMessageStore, StoredRsvpMessage
from butler.domains.rsvp.types import RoomEntry, rooms_from_legacy
from butler.rooms import ROOM_POLL_INTERVAL_SECONDS, fetch_botc_join_snapshot, is_botc_join_url
from butler.rooms.constants import (
    ROOM_DISCORD_EDIT_GAP_SECONDS,
    ROOM_REOPEN_WATCH_SECONDS,
    next_room_check_iso,
)
from butler.rooms.types import BotcJoinSnapshot
from butler.rsvp.controller import RsvpController
from butler.rsvp.runtime import drop_view, register_view, resolve_active_view
from butler.rsvp.view.event_message_view import EventMessageView
from butler.settings_store import GuildSettingsStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RoomPollSummary:
    messages_seen: int
    messages_with_botc_rooms: int
    rooms_checked: int
    rooms_failed: int
    messages_edited: int
    newly_opened: int
    elapsed_ms: float


def _rooms_for_state(view: EventMessageView) -> tuple[RoomEntry, ...]:
    state = view.view_state
    if state.rooms:
        return state.rooms
    return rooms_from_legacy(room_state=state.room_state, room_url=state.room_url)


def _botc_rooms(rooms: tuple[RoomEntry, ...]) -> tuple[RoomEntry, ...]:
    return tuple(room for room in rooms if is_botc_join_url(room.url))


def _rooms_to_pull(rooms: tuple[RoomEntry, ...], *, now_unix: int) -> tuple[RoomEntry, ...]:
    """Open/pending always; closed only while still in reopen-watch window."""
    pull: list[RoomEntry] = []
    for room in _botc_rooms(rooms):
        if room.state in {"open", "pending"}:
            pull.append(room)
            continue
        if room.state != "closed":
            continue
        closed_at = room.closed_at_unix
        if closed_at is None:
            pull.append(room)
            continue
        if now_unix - closed_at <= ROOM_REOPEN_WATCH_SECONDS:
            pull.append(room)
    return tuple(pull)


async def _fetch_updates_for_rooms(
    rooms: tuple[RoomEntry, ...],
    *,
    message_id: int,
    snapshot_by_url: dict[str, BotcJoinSnapshot | None],
) -> tuple[dict[str, BotcJoinSnapshot], int]:
    """Fetch join pages; reuse guild-wide URL cache so each botc URL is pulled once."""
    updates: dict[str, BotcJoinSnapshot] = {}
    failed = 0
    logger.debug(
        "room pull message_id=%s count=%s urls=%s",
        message_id,
        len(rooms),
        [room.url for room in rooms],
    )
    for room in rooms:
        cache_key = room.url.casefold()
        if cache_key in snapshot_by_url:
            snapshot = snapshot_by_url[cache_key]
            logger.debug(
                "room pull cache hit message_id=%s room_id=%s url=%s occupancy=%s",
                message_id,
                room.room_id,
                room.url,
                None if snapshot is None else snapshot.occupancy,
            )
        else:
            logger.debug(
                "room pull fetch message_id=%s room_id=%s url=%s state=%s hash=%s",
                message_id,
                room.room_id,
                room.url,
                room.state,
                room.content_hash,
            )
            snapshot = await fetch_botc_join_snapshot(
                room.url,
                room_id=room.room_id,
                message_id=message_id,
            )
            snapshot_by_url[cache_key] = snapshot
        if snapshot is None:
            failed += 1
            continue
        updates[room.room_id] = snapshot
    logger.debug(
        "room pull message done message_id=%s ok=%s failed=%s",
        message_id,
        len(updates),
        failed,
    )
    return updates, failed


async def _edit_rsvp_view(
    *,
    bot: commands.Bot,
    view: EventMessageView,
) -> bool:
    message_id = view.message_id
    channel_id = view.channel_id
    if message_id is None or channel_id is None:
        return False
    channel = bot.get_channel(channel_id)
    if channel is None:
        with suppress(discord.HTTPException, discord.NotFound, discord.Forbidden):
            channel = await bot.fetch_channel(channel_id)
    if not isinstance(
        channel,
        (
            discord.TextChannel,
            discord.VoiceChannel,
            discord.StageChannel,
            discord.Thread,
        ),
    ):
        return False
    try:
        partial = discord.PartialMessage(channel=channel, id=message_id)
        await partial.edit(view=view)
        return True
    except discord.NotFound:
        logger.debug(
            "room update edit skipped missing message_id=%s channel_id=%s",
            message_id,
            channel_id,
        )
        return False
    except (discord.HTTPException, TypeError):
        logger.exception(
            "Failed to edit RSVP after room poll message_id=%s channel_id=%s",
            message_id,
            channel_id,
        )
        return False


async def _resolve_view_for_stored(
    *,
    stored: StoredRsvpMessage,
    bot: commands.Bot,
    active_views: MutableMapping[int, EventMessageView],
    settings_store: GuildSettingsStore,
    view_store: RsvpMessageStore,
    controller: RsvpController,
) -> EventMessageView | None:
    existing = active_views.get(stored.message_id)
    if existing is not None:
        return existing
    return await resolve_active_view(
        message_id=stored.message_id,
        channel_id=stored.channel_id,
        active_views=active_views,
        bot=bot,
        settings_store=settings_store,
        view_store=view_store,
        controller=controller,
    )


@dataclass(frozen=True)
class _MessagePollOutcome:
    skipped: str | None = None
    with_botc: bool = False
    rooms_checked: int = 0
    rooms_failed: int = 0
    newly_opened: int = 0
    edited: bool = False


async def _poll_one_stored_message(
    *,
    stored: StoredRsvpMessage,
    bot: commands.Bot,
    view_store: RsvpMessageStore,
    controller: RsvpController,
    active_views: MutableMapping[int, EventMessageView],
    settings_store: GuildSettingsStore,
    snapshot_by_url: dict[str, BotcJoinSnapshot | None],
    now_unix: int,
    prior_edits: int,
) -> _MessagePollOutcome:
    view = await _resolve_view_for_stored(
        stored=stored,
        bot=bot,
        active_views=active_views,
        settings_store=settings_store,
        view_store=view_store,
        controller=controller,
    )
    if view is None:
        return _MessagePollOutcome(skipped="unresolved")

    all_rooms = _rooms_for_state(view)
    if not _botc_rooms(all_rooms):
        return _MessagePollOutcome(skipped="no_botc")
    pull_rooms = _rooms_to_pull(all_rooms, now_unix=now_unix)
    if not pull_rooms:
        return _MessagePollOutcome(skipped="inactive")

    updates, failed = await _fetch_updates_for_rooms(
        pull_rooms,
        message_id=stored.message_id,
        snapshot_by_url=snapshot_by_url,
    )
    if not updates:
        return _MessagePollOutcome(
            with_botc=True,
            rooms_checked=len(pull_rooms),
            rooms_failed=failed,
        )

    new_state, render, newly_opened, should_render = await controller.apply_room_poll_results(
        message_id=stored.message_id,
        view_state=view.view_state,
        updates=updates,
        now_unix=now_unix,
    )
    view.view_state = new_state
    logger.debug(
        "room pull applied message_id=%s fetched=%s failed=%s "
        "rooms_after=%s newly_opened=%s should_render=%s",
        stored.message_id,
        len(updates),
        failed,
        len(new_state.rooms),
        len(newly_opened),
        should_render,
    )
    if not should_render:
        return _MessagePollOutcome(
            with_botc=True,
            rooms_checked=len(pull_rooms),
            rooms_failed=failed,
            newly_opened=len(newly_opened),
        )

    # One Discord edit covers every room section on this RSVP message.
    view.apply_snapshot(render)
    register_view(view=view, active_views=active_views, bot=bot)
    if prior_edits > 0:
        await asyncio.sleep(ROOM_DISCORD_EDIT_GAP_SECONDS)

    edited = await _edit_rsvp_view(bot=bot, view=view)
    if edited:
        logger.debug(
            "room pull discord edit message_id=%s channel_id=%s",
            stored.message_id,
            stored.channel_id,
        )
    else:
        message = await fetch_message_from_channel(
            bot=bot,
            channel_id=stored.channel_id,
            message_id=stored.message_id,
        )
        if message is None:
            drop_view(message_id=stored.message_id, active_views=active_views)
            with suppress(OSError):
                view_store.delete_message(stored.message_id)
            logger.debug("room pull dropped missing RSVP message_id=%s", stored.message_id)

    return _MessagePollOutcome(
        with_botc=True,
        rooms_checked=len(pull_rooms),
        rooms_failed=failed,
        newly_opened=len(newly_opened),
        edited=edited,
    )


async def refresh_guild_botc_rooms(
    *,
    guild_id: int,
    bot: commands.Bot,
    view_store: RsvpMessageStore,
    controller: RsvpController,
    active_views: MutableMapping[int, EventMessageView],
    settings_store: GuildSettingsStore,
) -> RoomPollSummary:
    """Refetch botc join pages for active RSVP rooms; edit each changed message once."""
    started = time.perf_counter()
    try:
        stored_messages = [
            row for row in view_store.list_messages() if row.guild_id == guild_id
        ]
    except OSError:
        logger.exception("room poll failed to list messages guild=%s", guild_id)
        return RoomPollSummary(
            messages_seen=0,
            messages_with_botc_rooms=0,
            rooms_checked=0,
            rooms_failed=0,
            messages_edited=0,
            newly_opened=0,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
        )

    messages_with_botc = 0
    rooms_checked = 0
    rooms_failed = 0
    messages_edited = 0
    newly_opened_total = 0
    skipped_no_botc = 0
    skipped_inactive = 0
    skipped_unresolved = 0
    snapshot_by_url: dict[str, BotcJoinSnapshot | None] = {}
    checked_at = datetime.now(UTC)
    now_unix = int(checked_at.timestamp())
    next_check = next_room_check_iso(now=checked_at)
    logger.debug(
        "room pull batch start guild=%s messages=%s interval_s=%.0f "
        "checked_at=%s next_check=%s",
        guild_id,
        len(stored_messages),
        ROOM_POLL_INTERVAL_SECONDS,
        checked_at.isoformat(),
        next_check,
    )

    for stored in stored_messages:
        outcome = await _poll_one_stored_message(
            stored=stored,
            bot=bot,
            view_store=view_store,
            controller=controller,
            active_views=active_views,
            settings_store=settings_store,
            snapshot_by_url=snapshot_by_url,
            now_unix=now_unix,
            prior_edits=messages_edited,
        )
        if outcome.skipped == "unresolved":
            skipped_unresolved += 1
            continue
        if outcome.skipped == "no_botc":
            skipped_no_botc += 1
            continue
        if outcome.skipped == "inactive":
            skipped_inactive += 1
            continue
        if outcome.with_botc:
            messages_with_botc += 1
        rooms_checked += outcome.rooms_checked
        rooms_failed += outcome.rooms_failed
        newly_opened_total += outcome.newly_opened
        if outcome.edited:
            messages_edited += 1

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    logger.info(
        "room poll guild=%s seen=%s with_botc=%s checked=%s failed=%s "
        "edited=%s newly_opened=%s unique_urls=%s took_ms=%.1f next_check=%s",
        guild_id,
        len(stored_messages),
        messages_with_botc,
        rooms_checked,
        rooms_failed,
        messages_edited,
        newly_opened_total,
        len(snapshot_by_url),
        elapsed_ms,
        next_check,
    )
    logger.debug(
        "room pull batch done guild=%s skipped_no_botc=%s skipped_inactive=%s "
        "skipped_unresolved=%s edited=%s",
        guild_id,
        skipped_no_botc,
        skipped_inactive,
        skipped_unresolved,
        messages_edited,
    )
    return RoomPollSummary(
        messages_seen=len(stored_messages),
        messages_with_botc_rooms=messages_with_botc,
        rooms_checked=rooms_checked,
        rooms_failed=rooms_failed,
        messages_edited=messages_edited,
        newly_opened=newly_opened_total,
        elapsed_ms=elapsed_ms,
    )


async def refresh_connected_guild_botc_rooms(
    *,
    bot: commands.Bot,
    view_store: RsvpMessageStore,
    controller: RsvpController,
    active_views: MutableMapping[int, EventMessageView],
    settings_store: GuildSettingsStore,
) -> list[RoomPollSummary]:
    """Poll botc rooms for every guild the bot is currently connected to."""
    summaries: list[RoomPollSummary] = []
    for guild in bot.guilds:
        try:
            summary = await refresh_guild_botc_rooms(
                guild_id=guild.id,
                bot=bot,
                view_store=view_store,
                controller=controller,
                active_views=active_views,
                settings_store=settings_store,
            )
        except Exception:
            logger.exception("Automatic room poll failed guild=%s", guild.id)
            continue
        summaries.append(summary)
    return summaries
