import butler.design
from butler.event_logic import normalize_room_url
from butler.rsvp.AvailabilityView import AvailabilityView, build_user_mentions, can_manage_room_action
from butler.settings_store import GuildSettingsStore


import discord


from contextlib import suppress
from typing import ClassVar


class RoomLinkModal(discord.ui.Modal, title=butler.design.ROOM_LINK_MODAL_TITLE):
    """Modal for opening rooms"""
    room_link: ClassVar[discord.ui.TextInput[RoomLinkModal]] = discord.ui.TextInput(
        label=butler.design.ROOM_LINK_MODAL_LABEL,
        placeholder=butler.design.ROOM_LINK_MODAL_PLACEHOLDER,
        max_length=butler.design.ROOM_LINK_MODAL_MAX_LENGTH,
    )

    def __init__(self, view: AvailabilityView, settings_store: GuildSettingsStore) -> None:
        super().__init__()
        self.view = view
        self.settings_store = settings_store

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not can_manage_room_action(
            interaction,
            event_manager_role_id=interaction.guild_id
                and self.settings_store.get_event_manager_role_id(interaction.guild_id)
                or None,
        ):
            await interaction.response.send_message(
                self.view.open_room_permission_denied_message(interaction),
                ephemeral=True,
            )
            return
        raw_room_link = self.room_link.value.strip()
        try:
            normalized_room_url = normalize_room_url(raw_room_link)
        except ValueError:
            normalized_room_url = None

        if normalized_room_url is None:
            await interaction.response.send_message(
                butler.design.ROOM_LINK_MODAL_INVALID_MESSAGE,
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=False)
        await self.view.set_room_url(normalized_room_url)

        if interaction.message is not None:
            await self.view.rebuild(interaction)
        if interaction.message is None:
            print("Room-open announcement skipped: interaction message missing.")
            return

        statuses_to_ping: tuple[RsvpStatus, ...] = (
            "Available",
            "Maybe",
        )
        user_ids_to_ping: list[int] = []
        for status in statuses_to_ping:
            user_ids_to_ping.extend(await self.view.get_user_ids_for_status(status))
        mentions = build_user_mentions(user_ids_to_ping)
        message_link = interaction.message.jump_url

        channel = interaction.channel
        if isinstance(channel, (discord.TextChannel, discord.Thread)):
            if mentions:
                content = butler.design.ROOM_OPENED_WITH_MENTIONS_TEMPLATE.format(
                    mentions=mentions,
                    message_link=message_link,
                )
            else:
                content = butler.design.ROOM_OPENED_NO_MENTIONS_TEMPLATE.format(
                    message_link=message_link,
                )

            with suppress(discord.HTTPException):
                await channel.send(
                    content,
                    allowed_mentions=discord.AllowedMentions(
                        users=True,
                        roles=False,
                        everyone=False,
                    ),
                )
        print("Room link updated from RSVP modal.")