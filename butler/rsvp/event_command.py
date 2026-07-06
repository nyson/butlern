from __future__ import annotations

import datetime as dt
from collections.abc import Callable, MutableMapping
from typing import Final

import discord
from discord import app_commands
from discord.ext import commands

import butler.rsvp.runtime as rsvp_runtime
from butler.constants import (
    DEFAULT_EVENT_DURATION,
    DEFAULT_EVENT_LOCATION,
    DEFAULT_EVENT_START_TIME,
)
from butler.design import (
    DESIGN_PREVIEW_DEFAULT_DESCRIPTION,
    DESIGN_PREVIEW_DEFAULT_TITLE,
    EDITION_RESOURCE_ID_BY_NAME,
    EVENT_MANAGEMENT_PERMISSION_DENIED_MESSAGE,
    EVENT_MANAGEMENT_PERMISSION_DENIED_ROLE_TEMPLATE,
    RSVP_REACTION_EMOJIS,
)
from butler.event_logic import EventInput, resolve_event_input
from butler.permissions import (
    format_permissions,
    get_missing_event_permissions,
    get_missing_post_permissions,
    member_can_manage_events,
    permission_denied_message,
)
from butler.rsvp.rsvp_domain import RoomSnapshot
from butler.rsvp.rsvp_store import RsvpMessageStore
from butler.rsvp.rsvp_view import AvailabilityView
from butler.rsvp.types import ViewState
from butler.settings_store import GuildSettingsStore

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


def build_event_url(*, guild_id: int, event_id: int) -> str:
    return f"https://discord.com/events/{guild_id}/{event_id}"


def build_preview_event_url(*, guild_id: int, channel_id: int) -> str:
    return f"https://discord.com/channels/{guild_id}/{channel_id}"


def resolve_edition_media(
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


def configured_event_manager_role_id(
    *,
    settings_store: GuildSettingsStore,
    guild_id: int,
) -> int | None:
    return settings_store.get_event_manager_role_id(guild_id)


def event_management_permission_denied_message(
    *,
    guild: discord.Guild,
    event_manager_role_id: int | None,
) -> str:
    role = guild.get_role(event_manager_role_id) if event_manager_role_id is not None else None
    return permission_denied_message(
        role_mention=role.mention if role is not None else None,
        without_role=EVENT_MANAGEMENT_PERMISSION_DENIED_MESSAGE,
        with_role_template=EVENT_MANAGEMENT_PERMISSION_DENIED_ROLE_TEMPLATE,
    )


def resolve_configured_event_channel(
    *,
    settings_store: GuildSettingsStore,
    guild: discord.Guild,
    resolve_text_channel_fn: Callable[[discord.Guild, int], discord.TextChannel | None],
) -> discord.TextChannel | None:
    channel_id = settings_store.get_default_event_channel_id(guild.id)
    if channel_id is None:
        return None
    return resolve_text_channel_fn(guild, channel_id)


def missing_permission_details(
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
        details.append(f"Server-level missing: {format_permissions(missing_event_permissions)}")
    if missing_post_permissions:
        details.append(
            f"Missing in {event_channel.mention}: "
            f"{format_permissions(missing_post_permissions)}"
        )
    return details


async def defer_thinking_response(interaction: discord.Interaction) -> bool:
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


async def ensure_event_creation_permissions(
    *,
    interaction: discord.Interaction,
    guild: discord.Guild,
    event_channel: discord.TextChannel,
    bot: commands.Bot,
    get_bot_member_fn: Callable[[discord.Guild, discord.ClientUser | None], discord.Member | None],
) -> bool:
    bot_member = get_bot_member_fn(guild, bot.user)
    if bot_member is None:
        await interaction.followup.send(
            "I couldn't verify my server permissions. Re-invite the bot and try again.",
            ephemeral=True,
        )
        return False
    permission_errors = missing_permission_details(
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


async def resolve_event_command_context(
    interaction: discord.Interaction,
) -> tuple[discord.Guild, discord.Member] | None:
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "This command must be used in a server.",
            ephemeral=True,
        )
        return None
    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            "I couldn't verify your server member permissions.",
            ephemeral=True,
        )
        return None
    return guild, interaction.user


async def ensure_member_can_manage_events_for_command(
    *,
    interaction: discord.Interaction,
    guild: discord.Guild,
    member: discord.Member,
    event_manager_role_id: int | None,
) -> bool:
    if member_can_manage_events(
        member,
        event_manager_role_id=event_manager_role_id,
    ):
        return True
    await interaction.response.send_message(
        event_management_permission_denied_message(
            guild=guild,
            event_manager_role_id=event_manager_role_id,
        ),
        ephemeral=True,
    )
    return False


async def add_default_reactions(message: discord.Message) -> None:
    for emoji in RSVP_REACTION_EMOJIS:
        try:
            await message.add_reaction(emoji)
        except discord.HTTPException:
            continue


async def create_scheduled_event(
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


async def post_rsvp_message(
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
        content = await view.build_content()

        if embed is None:
            return await event_channel.send(
                content=content,
                view=view,
            )
        return await event_channel.send(
            content=content,
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


async def handle_event_command(
    *,
    interaction: discord.Interaction,
    title: str,
    description: str,
    edition: app_commands.Choice[str] | None,
    room_link: str | None,
    start_time: str | None,
    bot: commands.Bot,
    settings_store: GuildSettingsStore,
    view_store: RsvpMessageStore,
    active_views: MutableMapping[int, AvailabilityView],
    get_bot_member_fn: Callable[[discord.Guild, discord.ClientUser | None], discord.Member | None],
    resolve_text_channel_fn: Callable[[discord.Guild, int], discord.TextChannel | None],
) -> None:
    event_context = await resolve_event_command_context(interaction)
    if event_context is None:
        return
    guild, invoking_member = event_context

    event_manager_role_id = configured_event_manager_role_id(
        settings_store=settings_store,
        guild_id=guild.id,
    )
    if not await ensure_member_can_manage_events_for_command(
        interaction=interaction,
        guild=guild,
        member=invoking_member,
        event_manager_role_id=event_manager_role_id,
    ):
        return

    deferred = await defer_thinking_response(interaction)
    if not deferred:
        return

    event_channel = resolve_configured_event_channel(
        settings_store=settings_store,
        guild=guild,
        resolve_text_channel_fn=resolve_text_channel_fn,
    )
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

    if not await ensure_event_creation_permissions(
        interaction=interaction,
        guild=guild,
        event_channel=event_channel,
        bot=bot,
        get_bot_member_fn=get_bot_member_fn,
    ):
        return

    event_object = await create_scheduled_event(
        interaction=interaction,
        guild=guild,
        event_input=event_input,
    )
    if event_object is None:
        return

    event_url = build_event_url(guild_id=guild.id, event_id=event_object.id)
    selected_edition = edition.value if edition is not None else "Custom"
    selected_edition_emoji, selected_edition_image_url = resolve_edition_media(
        guild=guild,
        edition=selected_edition,
    )
    room_snapshot = RoomSnapshot.from_url(event_input.room_url)
    view_state = ViewState(
        event_name=event_object.name,
        start_unix=int(event_input.start_utc.timestamp()),
        event_url=event_url,
        edition=selected_edition,
        edition_emoji=selected_edition_emoji,
        room_state=room_snapshot.state,
        room_url=room_snapshot.url,
        edition_image_url=selected_edition_image_url,
        event_description=event_input.description,
    )
    view = AvailabilityView(
        view_state=view_state,
        settings_store=settings_store,
        view_store=view_store,
        timeout=None,
    )
    rsvp_message = await post_rsvp_message(
        interaction=interaction,
        event_channel=event_channel,
        view=view,
        event_link_message=event_url,
    )
    if rsvp_message is None:
        return

    persistence_warning = rsvp_runtime.bind_and_register_posted_view(
        view=view,
        message=rsvp_message,
        channel_id=event_channel.id,
        guild_id=guild.id,
        active_views=active_views,
        bot=bot,
    )
    await add_default_reactions(rsvp_message)
    await interaction.followup.send(
        (
            f"Created **{event_object.name}** and posted RSVP in "
            f"{event_channel.mention}: {rsvp_message.jump_url}"
        ),
        ephemeral=True,
    )
    if persistence_warning is not None:
        await interaction.followup.send(persistence_warning, ephemeral=True)


async def handle_previeweventdesign_command(
    *,
    interaction: discord.Interaction,
    title: str | None,
    description: str | None,
    edition: app_commands.Choice[str] | None,
    room_link: str | None,
    start_time: str | None,
    bot: commands.Bot,
    settings_store: GuildSettingsStore,
    view_store: RsvpMessageStore,
    active_views: MutableMapping[int, AvailabilityView],
    get_bot_member_fn: Callable[[discord.Guild, discord.ClientUser | None], discord.Member | None],
    resolve_text_channel_fn: Callable[[discord.Guild, int], discord.TextChannel | None],
) -> None:
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "This command must be used in a server.",
            ephemeral=True,
        )
        return

    deferred = await defer_thinking_response(interaction)
    if not deferred:
        return

    event_channel = resolve_configured_event_channel(
        settings_store=settings_store,
        guild=guild,
        resolve_text_channel_fn=resolve_text_channel_fn,
    )
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

    bot_member = get_bot_member_fn(guild, bot.user)
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

    preview_event_url = build_preview_event_url(
        guild_id=guild.id,
        channel_id=event_channel.id,
    )
    selected_edition = edition.value if edition is not None else "Custom"
    selected_edition_emoji, selected_edition_image_url = resolve_edition_media(
        guild=guild,
        edition=selected_edition,
    )
    room_snapshot = RoomSnapshot.from_url(preview_input.room_url)
    view_state = ViewState(
        event_name=preview_input.title,
        start_unix=int(preview_input.start_utc.timestamp()),
        event_url=preview_event_url,
        room_state=room_snapshot.state,
        room_url=room_snapshot.url,
        edition=selected_edition,
        edition_emoji=selected_edition_emoji,
        edition_image_url=selected_edition_image_url,
        event_description=preview_input.description,
    )
    view = AvailabilityView(
        view_state=view_state,
        settings_store=settings_store,
        view_store=view_store,
        timeout=None,
    )
    preview_message = await post_rsvp_message(
        interaction=interaction,
        event_channel=event_channel,
        view=view,
    )
    if preview_message is None:
        return

    persistence_warning = rsvp_runtime.bind_and_register_posted_view(
        view=view,
        message=preview_message,
        channel_id=event_channel.id,
        guild_id=guild.id,
        active_views=active_views,
        bot=bot,
    )
    await add_default_reactions(preview_message)
    await interaction.followup.send(
        f"Posted design preview in {event_channel.mention}: {preview_message.jump_url}",
        ephemeral=True,
    )
    if persistence_warning is not None:
        await interaction.followup.send(persistence_warning, ephemeral=True)
