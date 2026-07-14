from __future__ import annotations

import datetime as dt
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass
from typing import Final, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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

MAX_EVENT_AUTOCOMPLETE_CHOICES: Final[int] = 25
MAX_EVENT_CHOICE_NAME_LENGTH: Final[int] = 100
_CONNECTED_EVENT_CACHE: dict[int, int] = {}
_AUTOCOMPLETE_EVENT_CACHE: dict[int, list[tuple[str, str]]] = {}
CREATE_NEW_EVENT_CHOICE_LABEL: Final[str] = "Låt Butlern skapa ett evenemang!"
CREATE_NEW_EVENT_CHOICE_VALUE: Final[str] = "__butler_create_new_event__"
_SWEDISH_TIMEZONE: dt.tzinfo

try:
    _SWEDISH_TIMEZONE = ZoneInfo("Europe/Stockholm")
except ZoneInfoNotFoundError:
    _SWEDISH_TIMEZONE = dt.timezone(dt.timedelta(hours=1), name="CET")

EventResolutionSource = Literal["selected", "cache", "lookup"]


@dataclass(frozen=True)
class ExistingEventResolution:
    event: discord.ScheduledEvent | None
    source: EventResolutionSource | None
    error_message: str | None = None


def reset_connected_event_cache() -> None:
    _CONNECTED_EVENT_CACHE.clear()
    _AUTOCOMPLETE_EVENT_CACHE.clear()


def cached_connected_event_id(*, guild_id: int) -> int | None:
    return _CONNECTED_EVENT_CACHE.get(guild_id)


def cache_connected_event_id(*, guild_id: int, event_id: int) -> None:
    _CONNECTED_EVENT_CACHE[guild_id] = event_id


def clear_connected_event_id(*, guild_id: int) -> None:
    _CONNECTED_EVENT_CACHE.pop(guild_id, None)


def _drop_cached_event_option(*, guild_id: int, event_id: int) -> None:
    event_id_value = str(event_id)
    cached_options = _AUTOCOMPLETE_EVENT_CACHE.get(guild_id, [])
    _AUTOCOMPLETE_EVENT_CACHE[guild_id] = [
        option
        for option in cached_options
        if option[1] != event_id_value
    ]


def _upsert_cached_event_option(*, guild_id: int, event: discord.ScheduledEvent) -> None:
    event_id_value = str(event.id)
    existing_options = _AUTOCOMPLETE_EVENT_CACHE.get(guild_id, [])
    updated_options: list[tuple[str, str]] = [(_event_choice_name(event), event_id_value)]
    updated_options.extend(
        option
        for option in existing_options
        if option[1] != event_id_value
    )
    _AUTOCOMPLETE_EVENT_CACHE[guild_id] = updated_options


def _cache_reusable_events_for_guild(
    *,
    guild_id: int,
    events: list[discord.ScheduledEvent],
) -> None:
    if not events:
        clear_connected_event_id(guild_id=guild_id)
        _AUTOCOMPLETE_EVENT_CACHE[guild_id] = []
        return
    cache_connected_event_id(guild_id=guild_id, event_id=events[0].id)
    _AUTOCOMPLETE_EVENT_CACHE[guild_id] = [
        (_event_choice_name(event), str(event.id))
        for event in events
    ]


def build_event_url(*, guild_id: int, event_id: int) -> str:
    return f"https://discord.com/events/{guild_id}/{event_id}"


def build_preview_event_url(*, guild_id: int, channel_id: int) -> str:
    return f"https://discord.com/channels/{guild_id}/{channel_id}"


def _coerce_utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC)


def _event_start_utc(event: discord.ScheduledEvent) -> dt.datetime:
    return _coerce_utc(event.start_time)


def _is_reusable_scheduled_event(
    *,
    event: discord.ScheduledEvent,
    now_utc: dt.datetime,
) -> bool:
    if event.status not in {discord.EventStatus.active, discord.EventStatus.scheduled}:
        return False
    event_start_utc = _event_start_utc(event)
    event_start_swedish = event_start_utc.astimezone(_SWEDISH_TIMEZONE)
    now_swedish = now_utc.astimezone(_SWEDISH_TIMEZONE)
    if event_start_swedish.date() != now_swedish.date():
        return False
    return not (event.status == discord.EventStatus.scheduled and event_start_utc < now_utc)


def _event_sort_key(event: discord.ScheduledEvent) -> tuple[int, float, int]:
    status_priority = 0 if event.status == discord.EventStatus.active else 1
    start_timestamp = _event_start_utc(event).timestamp()
    return status_priority, start_timestamp, event.id


async def reusable_scheduled_events_for_guild(
    *,
    guild: discord.Guild,
) -> list[discord.ScheduledEvent]:
    try:
        events = await guild.fetch_scheduled_events(with_counts=False)
    except (discord.Forbidden, discord.HTTPException):
        return []

    now_utc = dt.datetime.now(dt.UTC)
    reusable_events = [
        event
        for event in events
        if _is_reusable_scheduled_event(event=event, now_utc=now_utc)
    ]
    reusable_events.sort(key=_event_sort_key)
    return reusable_events


async def fetch_scheduled_event_by_id(
    *,
    guild: discord.Guild,
    event_id: int,
) -> discord.ScheduledEvent | None:
    try:
        return await guild.fetch_scheduled_event(event_id, with_counts=False)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None


def _event_choice_name(event: discord.ScheduledEvent) -> str:
    swedish_start = _event_start_utc(event).astimezone(_SWEDISH_TIMEZONE)
    start_label = swedish_start.strftime("%H:%M")
    raw_name = f"{event.name} — {start_label}"
    if len(raw_name) <= MAX_EVENT_CHOICE_NAME_LENGTH:
        return raw_name
    return f"{raw_name[:MAX_EVENT_CHOICE_NAME_LENGTH - 1]}…"


async def autocomplete_existing_event(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    guild = interaction.guild
    if guild is None:
        return [
            app_commands.Choice(
                name=CREATE_NEW_EVENT_CHOICE_LABEL,
                value=CREATE_NEW_EVENT_CHOICE_VALUE,
            ),
        ]
    candidates = _AUTOCOMPLETE_EVENT_CACHE.get(guild.id, [])
    query = current.strip().casefold()
    if query:
        candidates = [
            option
            for option in candidates
            if query in option[0].casefold() or query in option[1]
        ]
    event_choices = [
        app_commands.Choice(name=name, value=value)
        for name, value in candidates[:MAX_EVENT_AUTOCOMPLETE_CHOICES]
    ]
    return [
        app_commands.Choice(
            name=CREATE_NEW_EVENT_CHOICE_LABEL,
            value=CREATE_NEW_EVENT_CHOICE_VALUE,
        ),
        *event_choices[: max(0, MAX_EVENT_AUTOCOMPLETE_CHOICES - 1)],
    ]


def _parse_selected_event_id(value: str) -> int | None:
    normalized_value = value.strip()
    if not normalized_value:
        return None
    try:
        return int(normalized_value)
    except ValueError:
        return None


async def _resolve_selected_existing_event(
    *,
    guild: discord.Guild,
    selected_event_value: str,
) -> ExistingEventResolution:
    event_id = _parse_selected_event_id(selected_event_value)
    if event_id is None:
        return ExistingEventResolution(
            event=None,
            source=None,
            error_message="Invalid `existing_event` value. Pick an event from autocomplete.",
        )
    event = await fetch_scheduled_event_by_id(guild=guild, event_id=event_id)
    if event is None:
        return ExistingEventResolution(
            event=None,
            source=None,
            error_message=(
                "I couldn't find that selected event anymore. "
                "Pick it again from autocomplete."
            ),
        )
    if not _is_reusable_scheduled_event(
        event=event,
        now_utc=dt.datetime.now(dt.UTC),
    ):
        _drop_cached_event_option(guild_id=guild.id, event_id=event.id)
        return ExistingEventResolution(
            event=None,
            source=None,
            error_message="That selected event is no longer active/upcoming.",
        )
    cache_connected_event_id(guild_id=guild.id, event_id=event.id)
    _upsert_cached_event_option(guild_id=guild.id, event=event)
    return ExistingEventResolution(event=event, source="selected")


async def _resolve_cached_existing_event(
    *,
    guild: discord.Guild,
) -> discord.ScheduledEvent | None:
    cached_event_id = cached_connected_event_id(guild_id=guild.id)
    if cached_event_id is None:
        return None
    event = await fetch_scheduled_event_by_id(guild=guild, event_id=cached_event_id)
    if event is None:
        clear_connected_event_id(guild_id=guild.id)
        _drop_cached_event_option(guild_id=guild.id, event_id=cached_event_id)
        return None
    if not _is_reusable_scheduled_event(
        event=event,
        now_utc=dt.datetime.now(dt.UTC),
    ):
        clear_connected_event_id(guild_id=guild.id)
        _drop_cached_event_option(guild_id=guild.id, event_id=cached_event_id)
        return None
    return event


async def resolve_existing_event_for_command(
    *,
    guild: discord.Guild,
    selected_event_value: str | None,
) -> ExistingEventResolution:
    if selected_event_value == CREATE_NEW_EVENT_CHOICE_VALUE:
        return ExistingEventResolution(event=None, source="selected")
    if selected_event_value is not None and selected_event_value.strip():
        return await _resolve_selected_existing_event(
            guild=guild,
            selected_event_value=selected_event_value,
        )

    cached_event = await _resolve_cached_existing_event(guild=guild)
    if cached_event is not None:
        return ExistingEventResolution(event=cached_event, source="cache")

    lookup_candidates = await reusable_scheduled_events_for_guild(guild=guild)
    _cache_reusable_events_for_guild(
        guild_id=guild.id,
        events=lookup_candidates,
    )
    if not lookup_candidates:
        return ExistingEventResolution(event=None, source=None)
    selected_event = lookup_candidates[0]
    return ExistingEventResolution(event=selected_event, source="lookup")


def event_start_unix(
    *,
    event: discord.ScheduledEvent,
) -> int:
    return int(_coerce_utc(event.start_time).timestamp())


async def warmup_connected_event_cache(*, bot: commands.Bot) -> None:
    warmed = 0
    empty = 0
    for guild in bot.guilds:
        candidates = await reusable_scheduled_events_for_guild(guild=guild)
        _cache_reusable_events_for_guild(
            guild_id=guild.id,
            events=candidates,
        )
        if not candidates:
            empty += 1
            print(f"Event cache warmup: guild={guild.id}, reusable_event=none")
            continue
        selected = candidates[0]
        warmed += 1
        print(
            f"Event cache warmup: guild={guild.id}, "
            f"reusable_event={selected.id} ({selected.name})"
        )
    print(f"Event cache warmup complete: cached={warmed}, empty={empty}.")


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


async def ensure_event_post_permissions(
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
    missing_permissions = get_missing_post_permissions(
        bot_member=bot_member,
        event_channel=event_channel,
    )
    if missing_permissions:
        await interaction.followup.send(
            (
                "I can't post the RSVP in "
                f"{event_channel.mention}: {format_permissions(missing_permissions)}"
            ),
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
    created_event: bool = True,
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
        if created_event:
            message = (
                f"Event created, but I can't post in {event_channel.mention}. "
                "Check `Send Messages`."
            )
        else:
            message = (
                f"I couldn't post the RSVP in {event_channel.mention}. "
                "Check `Send Messages`."
            )
        await interaction.followup.send(message, ephemeral=True)
        return None
    except discord.HTTPException as exc:
        message = (
            f"Event was created, but posting RSVP message failed: {exc}"
            if created_event
            else f"Posting RSVP message failed: {exc}"
        )
        await interaction.followup.send(message, ephemeral=True)
        return None


async def resolve_or_create_event_for_command(
    *,
    interaction: discord.Interaction,
    guild: discord.Guild,
    selected_event_value: str | None,
    event_input: EventInput,
    event_channel: discord.TextChannel,
    bot: commands.Bot,
    get_bot_member_fn: Callable[[discord.Guild, discord.ClientUser | None], discord.Member | None],
) -> tuple[discord.ScheduledEvent, bool] | None:
    existing_event_resolution = await resolve_existing_event_for_command(
        guild=guild,
        selected_event_value=selected_event_value,
    )
    if existing_event_resolution.error_message is not None:
        await interaction.followup.send(
            existing_event_resolution.error_message,
            ephemeral=True,
        )
        return None
    if existing_event_resolution.event is not None:
        if not await ensure_event_post_permissions(
            interaction=interaction,
            guild=guild,
            event_channel=event_channel,
            bot=bot,
            get_bot_member_fn=get_bot_member_fn,
        ):
            return None
        print(
            f"/event reused existing event {existing_event_resolution.event.id} "
            f"for guild {guild.id} (source={existing_event_resolution.source})."
        )
        _upsert_cached_event_option(
            guild_id=guild.id,
            event=existing_event_resolution.event,
        )
        return existing_event_resolution.event, False

    if not await ensure_event_creation_permissions(
        interaction=interaction,
        guild=guild,
        event_channel=event_channel,
        bot=bot,
        get_bot_member_fn=get_bot_member_fn,
    ):
        return None
    created_event = await create_scheduled_event(
        interaction=interaction,
        guild=guild,
        event_input=event_input,
    )
    if created_event is None:
        return None
    cache_connected_event_id(guild_id=guild.id, event_id=created_event.id)
    _upsert_cached_event_option(guild_id=guild.id, event=created_event)
    print(f"/event created and cached event {created_event.id} for guild {guild.id}.")
    return created_event, True


async def handle_event_command(
    *,
    interaction: discord.Interaction,
    title: str,
    description: str,
    edition: app_commands.Choice[str] | None,
    event: str,
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

    resolved_event = await resolve_or_create_event_for_command(
        interaction=interaction,
        guild=guild,
        selected_event_value=event,
        event_input=event_input,
        event_channel=event_channel,
        bot=bot,
        get_bot_member_fn=get_bot_member_fn,
    )
    if resolved_event is None:
        return
    event_object, created_event = resolved_event

    event_url = build_event_url(guild_id=guild.id, event_id=event_object.id)
    selected_edition = edition.value if edition is not None else "Custom"
    selected_edition_emoji, selected_edition_image_url = resolve_edition_media(
        guild=guild,
        edition=selected_edition,
    )
    room_snapshot = RoomSnapshot.from_url(event_input.room_url)
    view_state = ViewState(
        event_name=event_object.name,
        start_unix=event_start_unix(event=event_object),
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
        created_event=created_event,
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
    success_message = (
        (
            f"Created **{event_object.name}** and posted RSVP in "
            f"{event_channel.mention}: {rsvp_message.jump_url}"
        )
        if created_event
        else (
            f"Posted RSVP for **{event_object.name}** in "
            f"{event_channel.mention}: {rsvp_message.jump_url}"
        )
    )
    await interaction.followup.send(success_message, ephemeral=True)
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
        created_event=False,
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
