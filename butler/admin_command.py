from __future__ import annotations

import logging

import discord

from butler.caches.events import warmup_connected_event_cache
from butler.caches.events.option_cache import (
    AUTOCOMPLETE_EVENT_CACHE,
    event_option_cache_is_fresh,
)
from butler.command_helpers import defer_thinking_response
from butler.design import (
    REHYDRATE_FAILED_MESSAGE,
    REHYDRATE_GUILD_ONLY_MESSAGE,
    REHYDRATE_SUCCESS_TEMPLATE,
)

logger = logging.getLogger(__name__)


async def handle_rehydrate_command(*, interaction: discord.Interaction) -> None:
    """Force-warm the scheduled-event option cache for the current guild."""
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            REHYDRATE_GUILD_ONLY_MESSAGE,
            ephemeral=True,
        )
        return

    deferred = await defer_thinking_response(interaction)
    if not deferred:
        return

    try:
        result = await warmup_connected_event_cache(guilds=[guild], force=True)
    except Exception:
        logger.exception(
            "Manual event-cache rehydrate failed guild=%s",
            guild.id,
        )
        await interaction.followup.send(
            REHYDRATE_FAILED_MESSAGE,
            ephemeral=True,
        )
        return

    option_count = len(AUTOCOMPLETE_EVENT_CACHE.get(guild.id, []))
    fresh = event_option_cache_is_fresh(guild_id=guild.id)
    logger.info(
        "Manual rehydrate guild=%s options=%s fresh=%s result=%s",
        guild.id,
        option_count,
        fresh,
        result,
    )

    await interaction.followup.send(
        REHYDRATE_SUCCESS_TEMPLATE.format(
            warmed=result.warmed,
            empty=result.empty,
            skipped=result.skipped,
            failed=result.failed,
            elapsed_ms=result.elapsed_ms,
            options=option_count,
            fresh=fresh,
        ),
        ephemeral=True,
    )
