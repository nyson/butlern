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

from butler.command_helpers import defer_thinking_response
from butler.design import (
    ROOMS_PRUNE_FAILED_MESSAGE,
    ROOMS_PRUNE_SUCCESS_TEMPLATE,
    ROOMS_UPDATE_FAILED_MESSAGE,
    ROOMS_UPDATE_GUILD_ONLY_MESSAGE,
    ROOMS_UPDATE_PERMISSION_DENIED_MESSAGE,
    ROOMS_UPDATE_SUCCESS_TEMPLATE,
)
from butler.discord_helpers import fetch_message_from_channel
from butler.domains.rsvp.store import RsvpMessageStore, StoredRsvpMessage
from butler.rooms.constants import (
    ROOM_DISCORD_EDIT_GAP_SECONDS,
    ROOM_POLL_INTERVAL_SECONDS,
    next_room_check_iso,
)
from butler.rsvp.controller import RsvpController
from butler.rsvp.room_poll import refresh_guild_botc_rooms
from butler.rsvp.runtime import drop_view, register_view, resolve_active_view
from butler.rsvp.view.event_message_view import EventMessageView
from butler.settings_store import GuildSettingsStore

logger = logging.getLogger(__name__)


async def _resolve_view(
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


async def _edit_view(*, bot: commands.Bot, view: EventMessageView) -> bool:
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
        return False
    except (discord.HTTPException, TypeError):
        logger.exception(
            "Failed to edit RSVP after rooms prune message_id=%s channel_id=%s",
            message_id,
            channel_id,
        )
        return False


@dataclass(frozen=True)
class RoomPruneSummary:
    messages_seen: int
    messages_with_closed: int
    rooms_removed: int
    messages_edited: int
    elapsed_ms: float


async def prune_guild_closed_rooms(
    *,
    guild_id: int,
    bot: commands.Bot,
    view_store: RsvpMessageStore,
    controller: RsvpController,
    active_views: MutableMapping[int, EventMessageView],
    settings_store: GuildSettingsStore,
) -> RoomPruneSummary:
    """Remove every closed room section in the guild and untrack them from poll."""
    started = time.perf_counter()
    try:
        stored_messages = [
            row for row in view_store.list_messages() if row.guild_id == guild_id
        ]
    except OSError:
        logger.exception("rooms prune failed to list messages guild=%s", guild_id)
        return RoomPruneSummary(
            messages_seen=0,
            messages_with_closed=0,
            rooms_removed=0,
            messages_edited=0,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
        )

    now_unix = int(datetime.now(UTC).timestamp())
    messages_with_closed = 0
    rooms_removed = 0
    messages_edited = 0

    for stored in stored_messages:
        view = await _resolve_view(
            stored=stored,
            bot=bot,
            active_views=active_views,
            settings_store=settings_store,
            view_store=view_store,
            controller=controller,
        )
        if view is None:
            continue

        new_state, render, should_render, removed = await controller.prune_closed_rooms(
            message_id=stored.message_id,
            view_state=view.view_state,
            now_unix=now_unix,
            force_all_closed=True,
        )
        view.view_state = new_state
        if removed <= 0:
            continue
        messages_with_closed += 1
        rooms_removed += removed
        if not should_render:
            continue

        view.apply_snapshot(render)
        register_view(view=view, active_views=active_views, bot=bot)
        if messages_edited > 0:
            await asyncio.sleep(ROOM_DISCORD_EDIT_GAP_SECONDS)

        if await _edit_view(bot=bot, view=view):
            messages_edited += 1
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

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    logger.info(
        "rooms prune guild=%s seen=%s with_closed=%s removed=%s edited=%s took_ms=%.1f",
        guild_id,
        len(stored_messages),
        messages_with_closed,
        rooms_removed,
        messages_edited,
        elapsed_ms,
    )
    return RoomPruneSummary(
        messages_seen=len(stored_messages),
        messages_with_closed=messages_with_closed,
        rooms_removed=rooms_removed,
        messages_edited=messages_edited,
        elapsed_ms=elapsed_ms,
    )


async def handle_rooms_update_command(
    *,
    interaction: discord.Interaction,
    bot: commands.Bot,
    view_store: RsvpMessageStore,
    controller: RsvpController,
    active_views: MutableMapping[int, EventMessageView],
    settings_store: GuildSettingsStore,
) -> None:
    """Manually refetch botc join pages for RSVP rooms in the current guild."""
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            ROOMS_UPDATE_GUILD_ONLY_MESSAGE,
            ephemeral=True,
        )
        return

    perms = getattr(interaction.user, "guild_permissions", None)
    if perms is None or not perms.manage_guild:
        await interaction.response.send_message(
            ROOMS_UPDATE_PERMISSION_DENIED_MESSAGE,
            ephemeral=True,
        )
        return

    deferred = await defer_thinking_response(interaction)
    if not deferred:
        return

    next_check = next_room_check_iso()
    user = interaction.user
    logger.debug(
        "/rooms update invoked guild=%s user=%s interval_s=%.0f next_check=%s",
        guild.id,
        None if user is None else user.id,
        ROOM_POLL_INTERVAL_SECONDS,
        next_check,
    )

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
        logger.exception("Manual rooms update failed guild=%s", guild.id)
        await interaction.followup.send(
            ROOMS_UPDATE_FAILED_MESSAGE,
            ephemeral=True,
        )
        return

    next_check = next_room_check_iso()
    logger.debug(
        "/rooms update finished guild=%s edited=%s newly_opened=%s failed=%s next_check=%s",
        guild.id,
        summary.messages_edited,
        summary.newly_opened,
        summary.rooms_failed,
        next_check,
    )
    await interaction.followup.send(
        ROOMS_UPDATE_SUCCESS_TEMPLATE.format(
            elapsed_ms=summary.elapsed_ms,
            messages_seen=summary.messages_seen,
            messages_with_botc_rooms=summary.messages_with_botc_rooms,
            rooms_checked=summary.rooms_checked,
            rooms_failed=summary.rooms_failed,
            messages_edited=summary.messages_edited,
            newly_opened=summary.newly_opened,
        ),
        ephemeral=True,
    )


async def handle_rooms_prune_command(
    *,
    interaction: discord.Interaction,
    bot: commands.Bot,
    view_store: RsvpMessageStore,
    controller: RsvpController,
    active_views: MutableMapping[int, EventMessageView],
    settings_store: GuildSettingsStore,
) -> None:
    """Remove closed room sections from RSVP messages and untrack them."""
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            ROOMS_UPDATE_GUILD_ONLY_MESSAGE,
            ephemeral=True,
        )
        return

    perms = getattr(interaction.user, "guild_permissions", None)
    if perms is None or not perms.manage_guild:
        await interaction.response.send_message(
            ROOMS_UPDATE_PERMISSION_DENIED_MESSAGE,
            ephemeral=True,
        )
        return

    deferred = await defer_thinking_response(interaction)
    if not deferred:
        return

    user = interaction.user
    logger.debug(
        "/rooms prune invoked guild=%s user=%s",
        guild.id,
        None if user is None else user.id,
    )

    try:
        summary = await prune_guild_closed_rooms(
            guild_id=guild.id,
            bot=bot,
            view_store=view_store,
            controller=controller,
            active_views=active_views,
            settings_store=settings_store,
        )
    except Exception:
        logger.exception("Manual rooms prune failed guild=%s", guild.id)
        await interaction.followup.send(
            ROOMS_PRUNE_FAILED_MESSAGE,
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        ROOMS_PRUNE_SUCCESS_TEMPLATE.format(
            elapsed_ms=summary.elapsed_ms,
            messages_seen=summary.messages_seen,
            messages_with_closed=summary.messages_with_closed,
            rooms_removed=summary.rooms_removed,
            messages_edited=summary.messages_edited,
        ),
        ephemeral=True,
    )
