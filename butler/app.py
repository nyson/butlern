from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import butler.bot_events as bot_events
import butler.rsvp.event_command as rsvp_event_command
import butler.rsvp.settings_command as rsvp_settings_command
from butler.config import load_config
from butler.constants import PERSISTANCE_PATH
from butler.design import ONBOARDING_MESSAGE
from butler.discord_helpers import (
    get_bot_member,
    resolve_text_channel,
)
from butler.permissions import find_onboarding_channel
from butler.rsvp.rsvp_store import RsvpMessageStore
from butler.rsvp.rsvp_view import AvailabilityView
from butler.settings_store import GuildSettingsStore

CONFIG = load_config()
_force_guild_sync = False
SETTINGS_STORE = GuildSettingsStore.load(PERSISTANCE_PATH)
RSVP_MESSAGE_STORE = RsvpMessageStore.load(PERSISTANCE_PATH)
ACTIVE_RSVP_VIEWS: dict[int, AvailabilityView] = {}

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
    get_bot_member_fn=lambda guild, bot_user: get_bot_member(guild, bot_user),
    find_onboarding_channel_fn=lambda guild, bot_member: find_onboarding_channel(
        guild,
        bot_member,
    ),
    onboarding_message=ONBOARDING_MESSAGE,
)
on_ready = _REGISTERED_BOT_EVENTS.on_ready
on_guild_join = _REGISTERED_BOT_EVENTS.on_guild_join
on_raw_reaction_add = _REGISTERED_BOT_EVENTS.on_raw_reaction_add
on_raw_reaction_remove = _REGISTERED_BOT_EVENTS.on_raw_reaction_remove
setup_hook = _REGISTERED_BOT_EVENTS.setup_hook


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
    await rsvp_settings_command.handle_seteventchannel_command(
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
    await rsvp_settings_command.handle_seteventrole_command(
        interaction=interaction,
        role=role,
        settings_store=SETTINGS_STORE,
    )


@bot.tree.command(
    name="event",
    description="Create a planned event and post an RSVP message",
)
@app_commands.guild_only()
@app_commands.describe(
    title="Event title",
    description="Event description text",
    edition="Optional edition from BOTC resources",
    room_link="Optional room URL (http/https) to include in the post",
    start_time="Optional start time in 24h format HH:MM (default: 19:00)",
)
@app_commands.choices(edition=rsvp_event_command.BOTC_EDITION_CHOICES)
async def event(
    interaction: discord.Interaction,
    title: str,
    description: str,
    edition: app_commands.Choice[str] | None = None,
    room_link: str | None = None,
    start_time: str | None = None,
) -> None:
    await rsvp_event_command.handle_event_command(
        interaction=interaction,
        title=title,
        description=description,
        edition=edition,
        room_link=room_link,
        start_time=start_time,
        bot=bot,
        settings_store=SETTINGS_STORE,
        view_store=RSVP_MESSAGE_STORE,
        active_views=ACTIVE_RSVP_VIEWS,
        get_bot_member_fn=get_bot_member,
        resolve_text_channel_fn=resolve_text_channel,
    )


@bot.tree.command(
    name="previeweventdesign",
    description="Post an RSVP design preview without creating a scheduled event",
)
@app_commands.guild_only()
@app_commands.describe(
    title="Optional preview title",
    description="Optional preview description text",
    edition="Optional edition from BOTC resources",
    room_link="Optional room URL (http/https) to include in the preview",
    start_time="Optional preview start time in HH:MM (default: 19:00)",
)
@app_commands.choices(edition=rsvp_event_command.BOTC_EDITION_CHOICES)
async def previeweventdesign(
    interaction: discord.Interaction,
    title: str | None = None,
    description: str | None = None,
    edition: app_commands.Choice[str] | None = None,
    room_link: str | None = None,
    start_time: str | None = None,
) -> None:
    await rsvp_event_command.handle_previeweventdesign_command(
        interaction=interaction,
        title=title,
        description=description,
        edition=edition,
        room_link=room_link,
        start_time=start_time,
        bot=bot,
        settings_store=SETTINGS_STORE,
        view_store=RSVP_MESSAGE_STORE,
        active_views=ACTIVE_RSVP_VIEWS,
        get_bot_member_fn=get_bot_member,
        resolve_text_channel_fn=resolve_text_channel,
    )


def main(*, force_guild_sync: bool = False) -> None:
    global _force_guild_sync
    _force_guild_sync = force_guild_sync
    if not CONFIG.token:
        raise RuntimeError("Missing DISCORD_TOKEN in .env or the environment.")
    bot.run(CONFIG.token)


def main_dev() -> None:
    main(force_guild_sync=True)
