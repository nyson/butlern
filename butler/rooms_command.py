from __future__ import annotations

import logging
from collections.abc import MutableMapping

import discord
from discord.ext import commands

from butler.command_helpers import defer_thinking_response
from butler.design import (
    ROOMS_UPDATE_FAILED_MESSAGE,
    ROOMS_UPDATE_GUILD_ONLY_MESSAGE,
    ROOMS_UPDATE_SUCCESS_TEMPLATE,
)
from butler.domains.rsvp.store import RsvpMessageStore
from butler.rooms.constants import ROOM_POLL_INTERVAL_SECONDS, next_room_check_iso
from butler.rsvp.controller import RsvpController
from butler.rsvp.room_poll import refresh_guild_botc_rooms
from butler.rsvp.view.event_message_view import EventMessageView
from butler.settings_store import GuildSettingsStore

logger = logging.getLogger(__name__)


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

    # Batch just finished; report the following scheduled window from now.
    next_check = next_room_check_iso()
    logger.debug(
        "/rooms update finished guild=%s edited=%s newly_opened=%s "
        "failed=%s next_check=%s",
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
