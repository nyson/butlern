from __future__ import annotations

from collections.abc import Awaitable, Callable

import discord
from discord import ui

from butler.design import (
    ROOM_ANNOUNCE_MODAL_LINK_TEMPLATE,
    ROOM_ANNOUNCE_MODAL_TITLE,
    ROOM_LINK_MESSAGE_LABEL,
    ROOM_LINK_MESSAGE_MAX_LENGTH,
    ROOM_LINK_MESSAGE_PLACEHOLDER,
    ROOM_LINK_MODAL_LABEL,
    ROOM_LINK_MODAL_MAX_LENGTH,
    ROOM_LINK_MODAL_PLACEHOLDER,
    ROOM_LINK_MODAL_TITLE,
    ROOM_LINK_PING_NONE_LABEL,
    ROOM_LINK_PING_PLAYERS_LABEL,
    ROOM_LINK_PING_RSVP_DESCRIPTION,
    ROOM_LINK_PING_RSVP_LABEL,
    ROOM_LINK_PING_SELECT_DESCRIPTION,
    ROOM_LINK_PING_SELECT_LABEL,
)
from butler.rsvp.room_announce import DEFAULT_PING_MODE, PING_MODES, PingMode

SubmitRoomLink = Callable[[discord.Interaction, str, PingMode, str | None], Awaitable[None]]


def build_ping_select(
    *, custom_id: str, default: PingMode = DEFAULT_PING_MODE
) -> ui.Select[RoomLinkModal]:
    """Dropdown: Pinga inte / Pinga kvällens uppskrivna / Pinga alla @Spelare."""
    options = [
        discord.SelectOption(label=ROOM_LINK_PING_NONE_LABEL, value="none", emoji="🔕"),
        discord.SelectOption(
            label=ROOM_LINK_PING_RSVP_LABEL,
            value="rsvp",
            description=ROOM_LINK_PING_RSVP_DESCRIPTION,
            emoji="🗓️",
        ),
        discord.SelectOption(label=ROOM_LINK_PING_PLAYERS_LABEL, value="players", emoji="📢"),
    ]
    for option in options:
        option.default = option.value == default
    return ui.Select(custom_id=custom_id, options=options, min_values=1, max_values=1)


def parse_ping_mode(values: list[str]) -> PingMode:
    if values and values[0] in PING_MODES:
        return values[0]
    return DEFAULT_PING_MODE


class RoomLinkModal(ui.Modal):
    """Collect room URL, optional announce text, and who to ping.

    With ``fixed_room_url`` the URL is shown read-only (re-announce an open room).
    """

    def __init__(
        self,
        *,
        on_submit: SubmitRoomLink,
        fixed_room_url: str | None = None,
        default_ping_mode: PingMode = DEFAULT_PING_MODE,
    ) -> None:
        super().__init__(
            title=ROOM_LINK_MODAL_TITLE if fixed_room_url is None else ROOM_ANNOUNCE_MODAL_TITLE
        )
        self._on_submit = on_submit
        self._fixed_room_url = fixed_room_url

        self._room_link: ui.TextInput[RoomLinkModal] | None = None
        if fixed_room_url is None:
            self._room_link = ui.TextInput(
                label=ROOM_LINK_MODAL_LABEL,
                placeholder=ROOM_LINK_MODAL_PLACEHOLDER,
                max_length=ROOM_LINK_MODAL_MAX_LENGTH,
                required=True,
            )
            self.add_item(self._room_link)
        else:
            self.add_item(
                ui.TextDisplay(ROOM_ANNOUNCE_MODAL_LINK_TEMPLATE.format(room_url=fixed_room_url))
            )

        self._message: ui.TextInput[RoomLinkModal] = ui.TextInput(
            label=ROOM_LINK_MESSAGE_LABEL,
            placeholder=ROOM_LINK_MESSAGE_PLACEHOLDER,
            max_length=ROOM_LINK_MESSAGE_MAX_LENGTH,
            required=False,
            style=discord.TextStyle.paragraph,
        )
        self._ping = build_ping_select(
            custom_id="butler:rsvp:room-link-ping",
            default=default_ping_mode,
        )

        self.add_item(self._message)
        self.add_item(
            ui.Label(
                text=ROOM_LINK_PING_SELECT_LABEL,
                description=ROOM_LINK_PING_SELECT_DESCRIPTION,
                component=self._ping,
            )
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw_message = self._message.value.strip()
        optional_message = raw_message if raw_message else None
        room_url = (
            self._room_link.value if self._room_link is not None else self._fixed_room_url or ""
        )
        await self._on_submit(
            interaction,
            room_url,
            parse_ping_mode(self._ping.values),
            optional_message,
        )
