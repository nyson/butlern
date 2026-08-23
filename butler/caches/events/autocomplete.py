from __future__ import annotations

import logging

import discord
from discord import app_commands

from butler.caches.events.constants import MAX_EVENT_AUTOCOMPLETE_CHOICES
from butler.caches.events.option_cache import (
    AUTOCOMPLETE_EVENT_CACHE,
    event_option_cache_is_fresh,
)
from butler.design import (
    CREATE_NEW_EVENT_CHOICE_LABEL,
    CREATE_NEW_EVENT_CHOICE_VALUE,
    EVENT_AUTOCOMPLETE_ERROR_TEMPLATE,
)

logger = logging.getLogger(__name__)


class EventOptionCacheColdError(RuntimeError):
    """Raised when autocomplete runs before boot/gateway/daily warm completed."""


def create_new_choice() -> app_commands.Choice[str]:
    return app_commands.Choice(
        name=CREATE_NEW_EVENT_CHOICE_LABEL,
        value=CREATE_NEW_EVENT_CHOICE_VALUE,
    )


# NOSONAR - discord.py autocomplete callback is async by API contract.
async def autocomplete_existing_event(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Return cache-backed choices only.

    Commands that use this autocomplete must not be published while the event
    option cache is cold. A cold cache is a programming/ops error — raise.
    """
    try:
        guild = interaction.guild
        if guild is None:
            return [create_new_choice()]

        if not event_option_cache_is_fresh(guild_id=guild.id):
            raise EventOptionCacheColdError(
                f"Event option cache is cold for guild {guild.id}; "
                "slash commands should not be registered until warm."
            )

        candidates = list(AUTOCOMPLETE_EVENT_CACHE.get(guild.id, []))
        query = current.strip().casefold()
        if query:
            candidates = [
                option
                for option in candidates
                if query in option[0].casefold() or query in option[1]
            ]
        event_choices = [
            app_commands.Choice(name=name, value=value)
            for name, value in candidates[: max(0, MAX_EVENT_AUTOCOMPLETE_CHOICES - 1)]
        ]
        return [create_new_choice(), *event_choices]
    except EventOptionCacheColdError:
        raise
    except Exception as e:
        return [
            app_commands.Choice(
                name=EVENT_AUTOCOMPLETE_ERROR_TEMPLATE.format(error=repr(e)),
                value="fel",
            )
        ]
