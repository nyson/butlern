from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import butler.bot_events as bot_events
import butler.rsvp.event_command as rsvp_event_command
import butler.settings_command as settings_command
from butler.config import load_config
from butler.constants import PERSISTANCE_PATH
from butler.design import (
    EVENT_CREATE_OPTION_DESCRIPTION,
    EVENT_CREATE_SUBCOMMAND_DESCRIPTION,
    EVENT_GROUP_DESCRIPTION,
    EVENT_LINK_SUBCOMMAND_DESCRIPTION,
    EVENT_OPTION_DESCRIPTION,
    ONBOARDING_MESSAGE,
)
from butler.discord_events import BOTC_EDITION_CHOICES
from butler.discord_helpers import (
    get_bot_member,
    resolve_text_channel,
)
from butler.domains.rsvp.store import RsvpMessageStore
from butler.permissions import find_onboarding_channel
from butler.rsvp.controller import RsvpController
from butler.rsvp.view.event_message_view import EventMessageView
from butler.settings_store import GuildSettingsStore

CONFIG = load_config()
_force_guild_sync = False
SETTINGS_STORE = GuildSettingsStore.load(PERSISTANCE_PATH)
RSVP_MESSAGE_STORE = RsvpMessageStore.load(PERSISTANCE_PATH)
RSVP_CONTROLLER = RsvpController(store=RSVP_MESSAGE_STORE)
ACTIVE_RSVP_VIEWS: dict[int, EventMessageView] = {}

intents = discord.Intents.default()
bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents)
_BOT_EVENT_STATE = bot_events.BotEventState()
_REGISTERED_BOT_EVENTS = bot_events.register_bot_events(
    register_bot=bot,
    get_runtime_bot_fn=lambda: bot,
    config=CONFIG,
    is_force_guild_sync_fn=lambda: _force_guild_sync,
    state=_BOT_EVENT_STATE,
    get_active_views_fn=lambda: ACTIVE_RSVP_VIEWS,
    get_settings_store_fn=lambda: SETTINGS_STORE,
    get_view_store_fn=lambda: RSVP_MESSAGE_STORE,
    get_controller_fn=lambda: RSVP_CONTROLLER,
    get_bot_member_fn=get_bot_member,
    find_onboarding_channel_fn=find_onboarding_channel,
    onboarding_message=ONBOARDING_MESSAGE,
)
on_ready = _REGISTERED_BOT_EVENTS.on_ready
on_guild_join = _REGISTERED_BOT_EVENTS.on_guild_join
on_scheduled_event_create = _REGISTERED_BOT_EVENTS.on_scheduled_event_create
on_scheduled_event_delete = _REGISTERED_BOT_EVENTS.on_scheduled_event_delete
on_scheduled_event_update = _REGISTERED_BOT_EVENTS.on_scheduled_event_update
setup_hook = _REGISTERED_BOT_EVENTS.setup_hook


event_group = app_commands.Group(
    name="event",
    description=EVENT_GROUP_DESCRIPTION,
    guild_only=True,
)


@event_group.command(name="create", description=EVENT_CREATE_SUBCOMMAND_DESCRIPTION)
@app_commands.describe(
    title="Event title",
    description="Event description text",
    event=EVENT_CREATE_OPTION_DESCRIPTION,
    edition="Optional edition from BOTC resources",
    room_link="Optional room URL (http/https) to include in the post",
    start_time="Optional start time in 24h format HH:MM (default: 19:00)",
)
@app_commands.choices(edition=BOTC_EDITION_CHOICES)
@app_commands.autocomplete(event=rsvp_event_command.autocomplete_existing_or_create_event)
async def event_create(
    interaction: discord.Interaction,
    title: str,
    description: str,
    event: str | None = None,
    edition: app_commands.Choice[str] | None = None,
    room_link: str | None = None,
    start_time: str | None = None,
) -> None:
    await rsvp_event_command.handle_event_create_command(
        interaction=interaction,
        title=title,
        description=description,
        event=event,
        edition=edition,
        room_link=room_link,
        start_time=start_time,
        bot=bot,
        settings_store=SETTINGS_STORE,
        controller=RSVP_CONTROLLER,
        active_views=ACTIVE_RSVP_VIEWS,
        get_bot_member_fn=get_bot_member,
        resolve_text_channel_fn=resolve_text_channel,
    )


@event_group.command(name="link", description=EVENT_LINK_SUBCOMMAND_DESCRIPTION)
@app_commands.describe(
    event=EVENT_OPTION_DESCRIPTION,
    edition="Optional edition from BOTC resources",
    room_link="Optional room URL (http/https) to include in the post",
)
@app_commands.choices(edition=BOTC_EDITION_CHOICES)
@app_commands.autocomplete(event=rsvp_event_command.autocomplete_existing_event)
async def event_link(
    interaction: discord.Interaction,
    event: str,
    edition: app_commands.Choice[str] | None = None,
    room_link: str | None = None,
) -> None:
    await rsvp_event_command.handle_event_link_command(
        interaction=interaction,
        event=event,
        edition=edition,
        room_link=room_link,
        bot=bot,
        settings_store=SETTINGS_STORE,
        controller=RSVP_CONTROLLER,
        active_views=ACTIVE_RSVP_VIEWS,
        get_bot_member_fn=get_bot_member,
        resolve_text_channel_fn=resolve_text_channel,
    )


bot.tree.add_command(event_group)
# Back-compat aliases used by tests that invoke command callbacks directly.
event = event_group


@bot.tree.command(
    name="seteventchannel",
    description="Set the default channel for Butler event posts",
)
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.describe(event_channel="Text channel where Butler should post event updates")
async def seteventchannel(
    interaction: discord.Interaction,
    event_channel: discord.TextChannel,
) -> None:
    await settings_command.handle_seteventchannel_command(
        interaction=interaction,
        event_channel=event_channel,
        bot=bot,
        settings_store=SETTINGS_STORE,
        get_bot_member_fn=get_bot_member,
    )


@bot.tree.command(
    name="seteventrole",
    description="Set role allowed to create events and open/close rooms",
)
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.describe(
    role=(
        "Role allowed to create events and open/close rooms. "
        "Leave empty to clear."
    )
)
async def seteventrole(
    interaction: discord.Interaction,
    role: discord.Role | None = None,
) -> None:
    await settings_command.handle_seteventrole_command(
        interaction=interaction,
        role=role,
        settings_store=SETTINGS_STORE,
    )



def main(*, force_guild_sync: bool = False) -> None:
    global _force_guild_sync
    _force_guild_sync = force_guild_sync
    if not CONFIG.token:
        raise RuntimeError("Missing DISCORD_TOKEN in .env or the environment.")
    bot.run(CONFIG.token, root_logger=True)


def main_dev() -> None:
    main(force_guild_sync=True)
