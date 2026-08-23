from __future__ import annotations

import discord

from butler.design import (
    CREATE_NEW_EVENT_CHOICE_LABEL,
    CREATE_NEW_EVENT_CHOICE_VALUE,
)


def build_event_select_options(
    *,
    choices: list[tuple[str, str]],
    include_create_new: bool = False,
) -> list[discord.SelectOption]:
    """Build select options from cache pairs ``(label, value)``.

    Modal/button path defaults to existing events only (no create-new).
    """
    options: list[discord.SelectOption] = []
    if include_create_new:
        options.append(
            discord.SelectOption(
                label=CREATE_NEW_EVENT_CHOICE_LABEL[:100],
                value=CREATE_NEW_EVENT_CHOICE_VALUE,
            )
        )
    for name, value in choices:
        options.append(discord.SelectOption(label=name[:100], value=value))
    # Discord allows at most 25 select options.
    return options[:25]
