from __future__ import annotations

import datetime as dt
from typing import Final

import discord
from discord import app_commands
from discord.ext import commands

from butler.config import load_config
from butler.constants import (
    CONFIG_PATH,
    DEFAULT_EVENT_DURATION,
    DEFAULT_EVENT_LOCATION,
    DEFAULT_EVENT_START_TIME,
    SETTINGS_PATH,
)
from butler.design import (
    DESIGN_PREVIEW_DEFAULT_DESCRIPTION,
    DESIGN_PREVIEW_DEFAULT_TITLE,
    EDITION_RESOURCE_ID_BY_NAME,
    LATER_EMOJI,
    MAYBE_EMOJI,
    ONBOARDING_MESSAGE,
    RSVP_REACTION_EMOJIS,
    STORYTELLER_EMOJI,
)
from butler.discord_helpers import (
    fetch_message_from_channel,
    get_bot_member,
    resolve_text_channel,
)
from butler.event_logic import EventInput, resolve_event_input
from butler.permissions import (
    find_onboarding_channel,
    format_permissions,
    get_missing_event_permissions,
    get_missing_post_permissions,
    guild_sync_access_message,
)
from butler.rsvp.rsvp_domain import RsvpStatus
from butler.rsvp.rsvp_view import AvailabilityView
from butler.settings_store import GuildSettingsStore

CONFIG = load_config(CONFIG_PATH)
TOKEN: Final[str] = CONFIG.token
DEV_GUILD_ID: Final[int | None] = CONFIG.guild_id
_force_guild_sync = False
SETTINGS_STORE = GuildSettingsStore.load(SETTINGS_PATH)
ACTIVE_RSVP_VIEWS: dict[int, AvailabilityView] = {}
BOTC_EDITIONS: Final[tuple[str, ...]] = (
    "Trouble Brewing",
    "Bad Moon Rising",
    "Sects and Violets",
    "Custom",
)
BOTC_EDITION_CHOICES: Final[list[app_commands.Choice[str]]] = [
    app_commands.Choice(name=edition, value=edition)
    for edition in BOTC_EDITIONS
]

intents = discord.Intents.default()
bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents)


def _reaction_status_from_emoji(emoji: str) -> RsvpStatus:
    if emoji == MAYBE_EMOJI:
        return "Maybe"
    if emoji == LATER_EMOJI:
        return "Later"
    if emoji == STORYTELLER_EMOJI:
        return "Storyteller"
    return "Available"


def _build_event_url(*, guild_id: int, event_id: int) -> str:
    return f"https://discord.com/events/{guild_id}/{event_id}"


def _build_preview_event_url(*, guild_id: int, channel_id: int) -> str:
    return f"https://discord.com/channels/{guild_id}/{channel_id}"


def _resolve_edition_media(
    *,
    guild: discord.Guild,
    edition: str | None,
) -> tuple[str | None, str | None]:
    if edition is None:
        return None, None

    edition_resource_id = EDITION_RESOURCE_ID_BY_NAME.get(edition)
    if edition_resource_id is None:
        return None, None

    emoji = discord.utils.get(guild.emojis, name=edition_resource_id)
    if emoji is None and edition_resource_id != "custom":
        emoji = discord.utils.get(guild.emojis, name="custom")
    if emoji is None:
        return None, None
    return str(emoji), str(emoji.url)


# def _build_user_mentions(user_ids: list[int]) -> str:
#     unique_user_ids = sorted(set(user_ids))
#     return " ".join(f"<@{user_id}>" for user_id in unique_user_ids)

def _configured_event_manager_role_id(guild_id: int) -> int | None:
    return SETTINGS_STORE.get_event_manager_role_id(guild_id)


def _member_can_manage_events(
    *,
    member: discord.Member,
    event_manager_role_id: int | None,
) -> bool:
    if member.guild_permissions.manage_guild:
        return True
    if event_manager_role_id is None:
        return False
    return any(role.id == event_manager_role_id for role in member.roles)


def _event_management_permission_denied_message(
    *,
    guild: discord.Guild,
    event_manager_role_id: int | None,
) -> str:
    if event_manager_role_id is None:
        return (
            "Du behöver behörigheten `Hantera server` eller den konfigurerade "
            "storyteller-rollen för att skapa event och öppna eller stänga rum."
        )
    role = guild.get_role(event_manager_role_id)
    if role is None:
        return (
            "Du behöver behörigheten `Hantera server` eller den konfigurerade "
            "storyteller-rollen för att skapa event och öppna eller stänga rum."
        )
    return (
        "Du behöver behörigheten `Hantera server` eller rollen "
        f"{role.mention} för att skapa event och öppna eller stänga rum."
    )



async def _user_has_reaction(
    reaction: discord.Reaction,
    *,
    user_id: int,
) -> bool:
    async for user in reaction.users():
        if user.id == user_id:
            return True
    return False


async def _status_from_message_reactions(
    *,
    message: discord.Message,
    user_id: int,
) -> RsvpStatus | None:
    has_available_reaction = False
    has_later_reaction = False
    has_storyteller_reaction = False

    for reaction in message.reactions:
        if not await _user_has_reaction(reaction, user_id=user_id):
            continue

        emoji = str(reaction.emoji)
        if emoji == MAYBE_EMOJI:
            return "Maybe"
        if emoji == LATER_EMOJI:
            has_later_reaction = True
            continue
        if emoji == STORYTELLER_EMOJI:
            has_storyteller_reaction = True
            continue
        has_available_reaction = True

    if has_later_reaction:
        return "Later"
    if has_storyteller_reaction:
        return "Storyteller"
    if has_available_reaction:
        return "Available"
    return None


async def _edit_rsvp_message(
    *,
    message: discord.Message,
    view: AvailabilityView,
) -> None:
    try:
        await message.edit(
            content=view.build_content(),
            embed=view.build_embed(),
            view=view,
        )
    except discord.HTTPException:
        return



def _resolve_configured_event_channel(guild: discord.Guild) -> discord.TextChannel | None:
    channel_id = SETTINGS_STORE.get_default_event_channel_id(guild.id)
    if channel_id is None:
        return None
    return resolve_text_channel(guild, channel_id)


def _missing_permission_details(
    *,
    bot_member: discord.Member,
    event_channel: discord.TextChannel,
) -> list[str]:
    missing_event_permissions = get_missing_event_permissions(bot_member=bot_member)
    missing_post_permissions = get_missing_post_permissions(
        bot_member=bot_member,
        event_channel=event_channel,
    )
    details: list[str] = []
    if missing_event_permissions:
        details.append(
            f"Server-level missing: {format_permissions(missing_event_permissions)}"
        )
    if missing_post_permissions:
        details.append(
            f"Missing in {event_channel.mention}: {format_permissions(missing_post_permissions)}"
        )
    return details

async def _defer_thinking_response(interaction: discord.Interaction) -> bool:
    try:
        await interaction.response.defer(ephemeral=True, thinking=True)
        return True
    except discord.InteractionResponded:
        return True
    except discord.NotFound as exc:
        if exc.code != 10062:
            raise
        return False
    except discord.HTTPException:
        return False

async def _ensure_event_creation_permissions(
    *,
    interaction: discord.Interaction,
    guild: discord.Guild,
    event_channel: discord.TextChannel,
) -> bool:
    bot_member = get_bot_member(guild, bot.user)
    if bot_member is None:
        await interaction.followup.send(
            "I couldn't verify my server permissions. Re-invite the bot and try again.",
            ephemeral=True,
        )
        return False
    permission_errors = _missing_permission_details(
        bot_member=bot_member,
        event_channel=event_channel,
    )
    if permission_errors:
        await interaction.followup.send(
            "I don't have the required permissions to create and post this event.\n"
            + "\n".join(permission_errors),
            ephemeral=True,
        )
        return False
    return True


async def _sync_to_guild(guild_id: int, *, strict: bool) -> None:
    guild = discord.Object(id=guild_id)
    bot.tree.copy_global_to(guild=guild)
    try:
        await bot.tree.sync(guild=guild)
    except discord.Forbidden as exc:
        guidance = guild_sync_access_message(guild_id)
        if strict:
            raise RuntimeError(guidance) from exc
        print(f"WARNING: {guidance}")
        print("Falling back to global command sync.")
        await bot.tree.sync()
        print("Synced global commands.")
        return
    print(f"Synced commands to guild {guild_id}.")


async def _sync_commands_on_startup() -> None:
    if _force_guild_sync:
        if DEV_GUILD_ID is None:
            raise RuntimeError(
                "butler-dev requires DISCORD_GUILD_ID in .env or the environment."
            )
        await _sync_to_guild(DEV_GUILD_ID, strict=True)
        print("Forced dev mode command sync is active.")
        return

    if DEV_GUILD_ID is not None:
        await _sync_to_guild(DEV_GUILD_ID, strict=False)

    await bot.tree.sync()
    print("Synced global commands.")


async def _add_default_reactions(message: discord.Message) -> None:
    for emoji in RSVP_REACTION_EMOJIS:
        try:
            await message.add_reaction(emoji)
        except discord.HTTPException:
            continue


async def _create_scheduled_event(
    *,
    interaction: discord.Interaction,
    guild: discord.Guild,
    event_input: EventInput,
) -> discord.ScheduledEvent | None:
    try:
        return await guild.create_scheduled_event(
            name=event_input.title,
            start_time=event_input.start_utc,
            end_time=event_input.end_utc,
            entity_type=discord.EntityType.external,
            privacy_level=discord.PrivacyLevel.guild_only,
            description=event_input.description,
            location=DEFAULT_EVENT_LOCATION,
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "I can't create this scheduled event. Check bot permissions (`Create Events`).",
            ephemeral=True,
        )
        return None
    except discord.HTTPException as exc:
        await interaction.followup.send(
            f"Discord rejected event creation: {exc}",
            ephemeral=True,
        )
        return None


async def _post_rsvp_message(
    *,
    interaction: discord.Interaction,
    event_channel: discord.TextChannel,
    view: AvailabilityView,
    event_link_message: str | None = None,
) -> discord.Message | None:
    try:
        if event_link_message is not None:
            await event_channel.send(content=event_link_message)
        embed = view.build_embed()
        if embed is None:
            return await event_channel.send(content=view.build_content(), view=view)
        return await event_channel.send(
            content=view.build_content(),
            embed=embed,
            view=view,
        )
    except discord.Forbidden:
        await interaction.followup.send(
            f"Event created, but I can't post in {event_channel.mention}. "
            "Check `Send Messages`.",
            ephemeral=True,
        )
        return None
    except discord.HTTPException as exc:
        await interaction.followup.send(
            f"Event was created, but posting RSVP message failed: {exc}",
            ephemeral=True,
        )
        return None


@bot.event
async def on_ready() -> None:
    if bot.user is not None:
        print(f"Logged in as {bot.user} (ID: {bot.user.id})")


@bot.event
async def on_guild_join(guild: discord.Guild) -> None:
    bot_member = get_bot_member(guild, bot.user)
    if bot_member is None:
        return

    onboarding_channel = find_onboarding_channel(guild, bot_member)
    if onboarding_channel is None:
        return

    try:
        await onboarding_channel.send(ONBOARDING_MESSAGE)
    except discord.HTTPException:
        return


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent) -> None:
    if bot.user is not None and payload.user_id == bot.user.id:
        return

    view = ACTIVE_RSVP_VIEWS.get(payload.message_id)
    if view is None:
        return

    status = _reaction_status_from_emoji(str(payload.emoji))

    await view.set_user_response(user_id=payload.user_id, status=status)
    message = await fetch_message_from_channel(
        bot=bot,
        channel_id=payload.channel_id,
        message_id=payload.message_id,
    )
    if message is None:
        return

    await _edit_rsvp_message(message=message, view=view)


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent) -> None:
    if bot.user is not None and payload.user_id == bot.user.id:
        return

    view = ACTIVE_RSVP_VIEWS.get(payload.message_id)
    if view is None:
        return

    message = await fetch_message_from_channel(
        bot=bot,
        channel_id=payload.channel_id,
        message_id=payload.message_id,
    )
    if message is None:
        return

    resolved_status = await _status_from_message_reactions(
        message=message,
        user_id=payload.user_id,
    )
    if resolved_status is None:
        await view.remove_user_response(payload.user_id)
    else:
        await view.set_user_response(user_id=payload.user_id, status=resolved_status)

    await _edit_rsvp_message(message=message, view=view)


@bot.event
async def setup_hook() -> None:
    if not _force_guild_sync:
        bot.tree.remove_command(
            "previeweventdesign",
            type=discord.AppCommandType.chat_input,
        )
    await _sync_commands_on_startup()


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
    if interaction.guild is None:
        await interaction.response.send_message(
            "This command must be used in a server.",
            ephemeral=True,
        )
        return

    if event_channel.guild.id != interaction.guild.id:
        await interaction.response.send_message(
            "Select a text channel from this server.",
            ephemeral=True,
        )
        return

    bot_member = get_bot_member(interaction.guild, bot.user)
    if bot_member is None:
        await interaction.response.send_message(
            "I couldn't verify my server permissions. Re-invite the bot and try again.",
            ephemeral=True,
        )
        return

    missing_permissions = get_missing_post_permissions(
        bot_member=bot_member,
        event_channel=event_channel,
    )
    if missing_permissions:
        await interaction.response.send_message(
            "I can't use that channel yet. Missing permissions in "
            f"{event_channel.mention}: {format_permissions(missing_permissions)}",
            ephemeral=True,
        )
        return

    try:
        SETTINGS_STORE.set_default_event_channel_id(interaction.guild.id, event_channel.id)
    except OSError:
        await interaction.response.send_message(
            "Failed to save the channel setting. Try again.",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        f"Default event channel set to {event_channel.mention}. "
        "You can change it anytime with `/seteventchannel`.",
        ephemeral=True,
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
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "This command must be used in a server.",
            ephemeral=True,
        )
        return

    if role is not None and role.guild.id != guild.id:
        await interaction.response.send_message(
            "Select a role from this server.",
            ephemeral=True,
        )
        return

    try:
        if role is None:
            SETTINGS_STORE.clear_event_manager_role_id(guild.id)
        else:
            SETTINGS_STORE.set_event_manager_role_id(guild.id, role.id)
    except OSError:
        await interaction.response.send_message(
            "Failed to save the role setting. Try again.",
            ephemeral=True,
        )
        return

    if role is None:
        await interaction.response.send_message(
            "Cleared the event manager role. Only users with "
            "`Hantera server` can now create events and open/close rooms.",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        (
            f"Members with {role.mention} can now create events and open/close rooms "
            "(in addition to `Hantera server`)."
        ),
        ephemeral=True,
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
@app_commands.choices(edition=BOTC_EDITION_CHOICES)
async def event(
    interaction: discord.Interaction,
    title: str,
    description: str,
    edition: app_commands.Choice[str] | None = None,
    room_link: str | None = None,
    start_time: str | None = None,
) -> None:
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "This command must be used in a server.",
            ephemeral=True,
        )
        return
    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            "I couldn't verify your server member permissions.",
            ephemeral=True,
        )
        return

    event_manager_role_id = _configured_event_manager_role_id(guild.id)
    if not _member_can_manage_events(
        member=interaction.user,
        event_manager_role_id=event_manager_role_id,
    ):
        await interaction.response.send_message(
            _event_management_permission_denied_message(
                guild=guild,
                event_manager_role_id=event_manager_role_id,
            ),
            ephemeral=True,
        )
        return

    deferred = await _defer_thinking_response(interaction)
    if not deferred:
        return

    event_channel = _resolve_configured_event_channel(guild)
    if event_channel is None:
        await interaction.followup.send(
            "No valid default event channel is set. Run `/seteventchannel` first.",
            ephemeral=True,
        )
        return

    try:
        event_input = resolve_event_input(
            title=title,
            description=description,
            room_link=room_link,
            start_time=start_time,
            now_local=dt.datetime.now().astimezone(),
            default_start_time=DEFAULT_EVENT_START_TIME,
            default_duration=DEFAULT_EVENT_DURATION,
        )
    except ValueError as exc:
        await interaction.followup.send(str(exc), ephemeral=True)
        return

    can_create_event = await _ensure_event_creation_permissions(
        interaction=interaction,
        guild=guild,
        event_channel=event_channel,
    )
    if not can_create_event:
        return

    event = await _create_scheduled_event(
        interaction=interaction,
        guild=guild,
        event_input=event_input,
    )
    if event is None:
        return

    event_url = _build_event_url(guild_id=guild.id, event_id=event.id)
    selected_edition = edition.value if edition is not None else "Custom"
    selected_edition_emoji, selected_edition_image_url = _resolve_edition_media(
        guild=guild,
        edition=selected_edition,
    )
    view = AvailabilityView(
        event_name=event.name,
        start_unix=int(event_input.start_utc.timestamp()),
        event_url=event_url,
        room_url=event_input.room_url,
        edition=selected_edition,
        edition_emoji=selected_edition_emoji,
        edition_image_url=selected_edition_image_url,
        event_manager_role_id=event_manager_role_id,
        event_description=event_input.description,
    )
    rsvp_message = await _post_rsvp_message(
        interaction=interaction,
        event_channel=event_channel,
        view=view,
        event_link_message=event_url,
    )
    if rsvp_message is None:
        return

    ACTIVE_RSVP_VIEWS[rsvp_message.id] = view
    await _add_default_reactions(rsvp_message)
    await interaction.followup.send(
        (
            f"Created **{event.name}** and posted RSVP in "
            f"{event_channel.mention}: {rsvp_message.jump_url}"
        ),
        ephemeral=True,
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
@app_commands.choices(edition=BOTC_EDITION_CHOICES)
async def previeweventdesign(
    interaction: discord.Interaction,
    title: str | None = None,
    description: str | None = None,
    edition: app_commands.Choice[str] | None = None,
    room_link: str | None = None,
    start_time: str | None = None,
) -> None:
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "This command must be used in a server.",
            ephemeral=True,
        )
        return

    deferred = await _defer_thinking_response(interaction)
    if not deferred:
        return

    event_channel = _resolve_configured_event_channel(guild)
    if event_channel is None:
        await interaction.followup.send(
            "No valid default event channel is set. Run `/seteventchannel` first.",
            ephemeral=True,
        )
        return

    preview_title = (title or "").strip() or DESIGN_PREVIEW_DEFAULT_TITLE
    preview_description = (description or "").strip() or DESIGN_PREVIEW_DEFAULT_DESCRIPTION
    try:
        preview_input = resolve_event_input(
            title=preview_title,
            description=preview_description,
            room_link=room_link,
            start_time=start_time,
            now_local=dt.datetime.now().astimezone(),
            default_start_time=DEFAULT_EVENT_START_TIME,
            default_duration=DEFAULT_EVENT_DURATION,
        )
    except ValueError as exc:
        await interaction.followup.send(str(exc), ephemeral=True)
        return

    bot_member = get_bot_member(guild, bot.user)
    if bot_member is None:
        await interaction.followup.send(
            "I couldn't verify my server permissions. Re-invite the bot and try again.",
            ephemeral=True,
        )
        return

    missing_permissions = get_missing_post_permissions(
        bot_member=bot_member,
        event_channel=event_channel,
    )
    if missing_permissions:
        await interaction.followup.send(
            "I can't post the preview in "
            f"{event_channel.mention}: {format_permissions(missing_permissions)}",
            ephemeral=True,
        )
        return

    preview_event_url = _build_preview_event_url(
        guild_id=guild.id,
        channel_id=event_channel.id,
    )
    event_manager_role_id = _configured_event_manager_role_id(guild.id)
    selected_edition = edition.value if edition is not None else "Custom"
    selected_edition_emoji, selected_edition_image_url = _resolve_edition_media(
        guild=guild,
        edition=selected_edition,
    )
    view = AvailabilityView(
        event_name=preview_input.title,
        start_unix=int(preview_input.start_utc.timestamp()),
        event_url=preview_event_url,
        room_url=preview_input.room_url,
        edition=selected_edition,
        edition_emoji=selected_edition_emoji,
        edition_image_url=selected_edition_image_url,
        event_manager_role_id=event_manager_role_id,
        event_description=preview_input.description,
    )
    preview_message = await _post_rsvp_message(
        interaction=interaction,
        event_channel=event_channel,
        view=view,
    )
    if preview_message is None:
        return

    ACTIVE_RSVP_VIEWS[preview_message.id] = view
    await _add_default_reactions(preview_message)
    await interaction.followup.send(
        f"Posted design preview in {event_channel.mention}: {preview_message.jump_url}",
        ephemeral=True,
    )


def main(*, force_guild_sync: bool = False) -> None:
    global _force_guild_sync
    _force_guild_sync = force_guild_sync
    if not TOKEN:
        raise RuntimeError("Missing DISCORD_TOKEN in .env or the environment.")
    bot.run(TOKEN)


def main_dev() -> None:
    main(force_guild_sync=True)
