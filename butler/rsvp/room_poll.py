from __future__ import annotations

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
from butler.rooms.constants import next_room_check_iso
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


async def _fetch_updates_for_rooms(
    rooms: tuple[RoomEntry, ...],
    *,
    message_id: int,
) -> tuple[dict[str, BotcJoinSnapshot], int]:
    updates: dict[str, BotcJoinSnapshot] = {}
    failed = 0
    for room in rooms:
        logger.debug(
            "room update fetch start message_id=%s room_id=%s url=%s "
            "prev_state=%s prev_occupancy=%s",
            message_id,
            room.room_id,
            room.url,
            room.state,
            None if room.botc is None else room.botc.occupancy,
        )
        snapshot = await fetch_botc_join_snapshot(
            room.url,
            room_id=room.room_id,
            message_id=message_id,
        )
        if snapshot is None:
            failed += 1
            logger.debug(
                "room update fetch miss message_id=%s room_id=%s url=%s",
                message_id,
                room.room_id,
                room.url,
            )
            continue
        logger.debug(
            "room update fetch ok message_id=%s room_id=%s occupancy=%s "
            "state_before=%s edition=%r phase=%r open_seats=%s",
            message_id,
            room.room_id,
            snapshot.occupancy,
            room.state,
            snapshot.edition,
            snapshot.phase,
            snapshot.open_seats,
        )
        updates[room.room_id] = snapshot
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
    except (discord.HTTPException, discord.NotFound, TypeError):
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


async def refresh_guild_botc_rooms(
    *,
    guild_id: int,
    bot: commands.Bot,
    view_store: RsvpMessageStore,
    controller: RsvpController,
    active_views: MutableMapping[int, EventMessageView],
    settings_store: GuildSettingsStore,
) -> RoomPollSummary:
    """Refetch botc join pages for all RSVP messages in ``guild_id`` and edit posts."""
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
    checked_at = datetime.now(UTC)
    now_unix = int(checked_at.timestamp())
    next_check = next_room_check_iso(now=checked_at)
    logger.debug(
        "room update batch start guild=%s messages=%s interval_s=%.0f "
        "checked_at=%s next_check=%s",
        guild_id,
        len(stored_messages),
        ROOM_POLL_INTERVAL_SECONDS,
        checked_at.isoformat(),
        next_check,
    )

    for stored in stored_messages:
        view = await _resolve_view_for_stored(
            stored=stored,
            bot=bot,
            active_views=active_views,
            settings_store=settings_store,
            view_store=view_store,
            controller=controller,
        )
        if view is None:
            logger.debug(
                "room update skip unresolved message_id=%s channel_id=%s guild=%s",
                stored.message_id,
                stored.channel_id,
                guild_id,
            )
            continue

        botc_rooms = _botc_rooms(_rooms_for_state(view))
        if not botc_rooms:
            logger.debug(
                "room update skip no botc rooms message_id=%s guild=%s",
                stored.message_id,
                guild_id,
            )
            continue
        messages_with_botc += 1
        rooms_checked += len(botc_rooms)
        logger.debug(
            "room update message start message_id=%s channel_id=%s "
            "botc_rooms=%s next_check=%s",
            stored.message_id,
            stored.channel_id,
            len(botc_rooms),
            next_check,
        )

        updates, failed = await _fetch_updates_for_rooms(
            botc_rooms,
            message_id=stored.message_id,
        )
        rooms_failed += failed
        if not updates:
            logger.debug(
                "room update message no successful fetches message_id=%s "
                "failed=%s next_check=%s",
                stored.message_id,
                failed,
                next_check,
            )
            continue

        new_state, render, newly_opened, should_render = await controller.apply_room_poll_results(
            message_id=stored.message_id,
            view_state=view.view_state,
            updates=updates,
            now_unix=now_unix,
        )
        # Always keep in-memory state in sync with fetch/persist; render separately.
        view.view_state = new_state
        newly_opened_total += len(newly_opened)
        logger.debug(
            "room update applied message_id=%s fetched=%s failed=%s "
            "rooms_after=%s newly_opened=%s should_render=%s states=%s next_check=%s",
            stored.message_id,
            len(updates),
            failed,
            len(new_state.rooms),
            len(newly_opened),
            should_render,
            [(room.room_id, room.state) for room in new_state.rooms],
            next_check,
        )

        if not should_render:
            logger.debug(
                "room update skip discord edit (no display change) message_id=%s",
                stored.message_id,
            )
            continue

        view.apply_snapshot(render)
        # Keep active registry in sync with rebuilt components.
        register_view(view=view, active_views=active_views, bot=bot)

        if await _edit_rsvp_view(bot=bot, view=view):
            messages_edited += 1
            logger.debug(
                "room update message edited message_id=%s channel_id=%s next_check=%s",
                stored.message_id,
                stored.channel_id,
                next_check,
            )
        else:
            logger.debug(
                "room update message edit failed message_id=%s channel_id=%s",
                stored.message_id,
                stored.channel_id,
            )
            # Message may be gone — prove it and drop stale store row.
            message = await fetch_message_from_channel(
                bot=bot,
                channel_id=stored.channel_id,
                message_id=stored.message_id,
            )
            if message is None:
                drop_view(message_id=stored.message_id, active_views=active_views)
                with suppress(OSError):
                    view_store.delete_message(stored.message_id)
                logger.debug(
                    "room update dropped missing RSVP message_id=%s",
                    stored.message_id,
                )

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    logger.info(
        "room poll guild=%s seen=%s with_botc=%s checked=%s failed=%s "
        "edited=%s newly_opened=%s took_ms=%.1f next_check=%s",
        guild_id,
        len(stored_messages),
        messages_with_botc,
        rooms_checked,
        rooms_failed,
        messages_edited,
        newly_opened_total,
        elapsed_ms,
        next_check,
    )
    logger.debug(
        "room update batch done guild=%s interval_s=%.0f checked_at=%s next_check=%s",
        guild_id,
        ROOM_POLL_INTERVAL_SECONDS,
        checked_at.isoformat(),
        next_check,
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
