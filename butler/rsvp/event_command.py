from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable, MutableMapping

import discord
from discord import app_commands
from discord.ext import commands

from butler.caches.events import (
    autocomplete_existing_event as autocomplete_existing_event,
)
from butler.caches.events import (
    autocomplete_existing_or_create_event as autocomplete_existing_or_create_event,
)
from butler.caches.events import (
    cache_connected_event_id,
    resolve_existing_event_for_command,
    upsert_cached_event_option,
)
from butler.caches.events import (
    cached_connected_event_id as cached_connected_event_id,
)
from butler.caches.events import (
    reset_connected_event_cache as reset_connected_event_cache,
)
from butler.caches.events import (
    warmup_connected_event_cache as warmup_connected_event_cache,
)
from butler.command_helpers import (
    configured_event_manager_role_id,
    defer_thinking_response,
    ensure_event_creation_permissions,
    ensure_event_post_permissions,
    ensure_member_can_manage_events_for_command,
    resolve_configured_event_channel,
    resolve_event_command_context,
)
from butler.constants import DEFAULT_EVENT_DURATION, DEFAULT_EVENT_START_TIME
from butler.design import (
    CREATE_NEW_EVENT_CHOICE_LABEL as CREATE_NEW_EVENT_CHOICE_LABEL,
)
from butler.design import (
    CREATE_NEW_EVENT_CHOICE_VALUE as CREATE_NEW_EVENT_CHOICE_VALUE,
)
from butler.design import (
    EVENT_CARD_PLACEHOLDER_MESSAGE as EVENT_CARD_PLACEHOLDER_MESSAGE,
)
from butler.design import (
    EVENT_LINK_MISSING_DESCRIPTION_FALLBACK as EVENT_LINK_MISSING_DESCRIPTION_FALLBACK,
)
from butler.design import (
    EVENT_LINK_REQUIRED_MESSAGE as EVENT_LINK_REQUIRED_MESSAGE,
)
from butler.design import (
    SELECT_EVENT_BUTTON_LABEL as SELECT_EVENT_BUTTON_LABEL,
)
from butler.discord_events import (
    build_event_url,
    build_preview_event_url,
    create_scheduled_event,
    event_start_unix,
    resolve_edition_media,
)
from butler.domains.rsvp.domain import RoomSnapshot
from butler.domains.rsvp.types import ViewState
from butler.event_logic import EventInput, normalize_room_url, resolve_event_input
from butler.rsvp.controller import RsvpController
from butler.rsvp.runtime import bind_and_register_posted_view
from butler.rsvp.view.event_message_view import EventMessageView
from butler.settings_store import GuildSettingsStore

logger = logging.getLogger(__name__)


async def post_rsvp_message(
    *,
    interaction: discord.Interaction,
    event_channel: discord.TextChannel,
    view: EventMessageView,
    event_url: str | None = None,
    created_event: bool = True,
) -> discord.Message | None:
    """Post companion slot first, then RSVP LayoutView.

    Companion is either the bare scheduled-event URL (native Discord card) or a
    placeholder, with **Koppla evenemang** attached. Posting it above the RSVP
    keeps late-link order correct.
    """
    try:
        companion_content = (
            event_url
            if event_url is not None and "/events/" in event_url
            else EVENT_CARD_PLACEHOLDER_MESSAGE
        )
        card_message = await event_channel.send(
            content=companion_content,
            view=view.build_event_card_view(),
        )
        view.set_event_card_message_id(card_message.id)
        return await event_channel.send(view=view)
    except discord.Forbidden:
        if created_event:
            message = (
                f"Event created, but I can't post in {event_channel.mention}. "
                "Check `Send Messages`."
            )
        else:
            message = f"I couldn't post the RSVP in {event_channel.mention}. Check `Send Messages`."
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


async def _create_scheduled_event_for_command(
    *,
    interaction: discord.Interaction,
    guild: discord.Guild,
    event_input: EventInput,
    event_channel: discord.TextChannel,
    bot: commands.Bot,
    get_bot_member_fn: Callable[[discord.Guild, discord.ClientUser | None], discord.Member | None],
) -> tuple[discord.ScheduledEvent | None, str]:
    """Create a Discord scheduled event. Returns (event|None, event_url)."""
    if not await ensure_event_creation_permissions(
        interaction=interaction,
        guild=guild,
        event_channel=event_channel,
        bot=bot,
        get_bot_member_fn=get_bot_member_fn,
    ):
        return None, ""
    created = await create_scheduled_event(
        interaction=interaction,
        guild=guild,
        event_input=event_input,
    )
    if created is None:
        return None, ""
    cache_connected_event_id(guild_id=guild.id, event_id=created.id)
    upsert_cached_event_option(guild_id=guild.id, event=created)
    logger.info("/event create created event %s for guild %s", created.id, guild.id)
    return created, build_event_url(guild_id=guild.id, event_id=created.id)


async def _post_unlinked_rsvp_slot(
    *,
    interaction: discord.Interaction,
    guild: discord.Guild,
    event_channel: discord.TextChannel,
    bot: commands.Bot,
    get_bot_member_fn: Callable[[discord.Guild, discord.ClientUser | None], discord.Member | None],
) -> str:
    """Permissions + placeholder channel URL when no scheduled event is linked yet."""
    if not await ensure_event_post_permissions(
        interaction=interaction,
        guild=guild,
        event_channel=event_channel,
        bot=bot,
        get_bot_member_fn=get_bot_member_fn,
    ):
        return ""
    return build_preview_event_url(guild_id=guild.id, channel_id=event_channel.id)


async def _resolve_create_command_event(
    *,
    interaction: discord.Interaction,
    guild: discord.Guild,
    selected_event_value: str | None,
    event_input: EventInput,
    event_channel: discord.TextChannel,
    bot: commands.Bot,
    get_bot_member_fn: Callable[[discord.Guild, discord.ClientUser | None], discord.Member | None],
) -> tuple[discord.ScheduledEvent | None, bool, str]:
    """Return (event|None, created, event_url) for `/event create`.

    - omitted/empty ``event`` → unlinked RSVP + companion placeholder
    - create-new sentinel → create Discord scheduled event
    - existing event id → link that event
    """
    if selected_event_value is None or not selected_event_value.strip():
        placeholder = await _post_unlinked_rsvp_slot(
            interaction=interaction,
            guild=guild,
            event_channel=event_channel,
            bot=bot,
            get_bot_member_fn=get_bot_member_fn,
        )
        return None, False, placeholder

    if selected_event_value == CREATE_NEW_EVENT_CHOICE_VALUE:
        created, event_url = await _create_scheduled_event_for_command(
            interaction=interaction,
            guild=guild,
            event_input=event_input,
            event_channel=event_channel,
            bot=bot,
            get_bot_member_fn=get_bot_member_fn,
        )
        if created is None or not event_url:
            return None, False, ""
        return created, True, event_url

    linked, event_url = await _link_existing_event_for_command(
        interaction=interaction,
        guild=guild,
        selected_event_value=selected_event_value,
        event_channel=event_channel,
        bot=bot,
        get_bot_member_fn=get_bot_member_fn,
    )
    if linked is None or not event_url:
        return None, False, ""
    return linked, False, event_url


async def _link_existing_event_for_command(
    *,
    interaction: discord.Interaction,
    guild: discord.Guild,
    selected_event_value: str,
    event_channel: discord.TextChannel,
    bot: commands.Bot,
    get_bot_member_fn: Callable[[discord.Guild, discord.ClientUser | None], discord.Member | None],
) -> tuple[discord.ScheduledEvent | None, str]:
    """Resolve an existing Discord scheduled event. Returns (event|None, event_url)."""
    if not selected_event_value.strip():
        await interaction.followup.send(EVENT_LINK_REQUIRED_MESSAGE, ephemeral=True)
        return None, ""

    resolution = await resolve_existing_event_for_command(
        guild=guild,
        selected_event_value=selected_event_value,
    )
    if resolution.error_message is not None:
        await interaction.followup.send(resolution.error_message, ephemeral=True)
        return None, ""
    if resolution.event is None:
        await interaction.followup.send(EVENT_LINK_REQUIRED_MESSAGE, ephemeral=True)
        return None, ""

    if not await ensure_event_post_permissions(
        interaction=interaction,
        guild=guild,
        event_channel=event_channel,
        bot=bot,
        get_bot_member_fn=get_bot_member_fn,
    ):
        return None, ""

    event_object = resolution.event
    upsert_cached_event_option(guild_id=guild.id, event=event_object)
    logger.info(
        "/event link linked existing event %s for guild %s (source=%s)",
        event_object.id,
        guild.id,
        resolution.source,
    )
    return (
        event_object,
        build_event_url(guild_id=guild.id, event_id=event_object.id),
    )


def _event_input_from_scheduled_event(
    *,
    event_object: discord.ScheduledEvent,
    room_link: str | None,
) -> EventInput:
    """Build EventInput from a Discord scheduled event's name/description/start."""
    description = (
        event_object.description or ""
    ).strip() or EVENT_LINK_MISSING_DESCRIPTION_FALLBACK
    start_utc = event_object.start_time
    if start_utc.tzinfo is None:
        start_utc = start_utc.replace(tzinfo=dt.UTC)
    else:
        start_utc = start_utc.astimezone(dt.UTC)
    start_local = start_utc.astimezone()
    room_url = None
    if room_link is not None and room_link.strip():
        room_url = normalize_room_url(room_link)
    return EventInput(
        title=event_object.name,
        description=description,
        room_url=room_url,
        start_local=start_local,
        start_utc=start_utc,
        end_utc=start_utc + DEFAULT_EVENT_DURATION,
    )


def _view_state_for_event(
    *,
    event_object: discord.ScheduledEvent | None,
    event_input: EventInput,
    event_url: str,
    selected_edition: str,
    selected_edition_emoji: str | None,
    selected_edition_image_url: str | None,
    room_snapshot: RoomSnapshot,
) -> tuple[ViewState, str]:
    """Build view state and display name."""
    if event_object is not None:
        return (
            ViewState(
                event_name=event_object.name,
                start_unix=event_start_unix(event=event_object),
                event_url=event_url,
                edition=selected_edition,
                edition_emoji=selected_edition_emoji,
                room_state=room_snapshot.state,
                room_url=room_snapshot.url,
                edition_image_url=selected_edition_image_url,
                event_description=event_input.description,
            ),
            event_object.name,
        )
    return (
        ViewState(
            event_name=event_input.title,
            start_unix=int(event_input.start_utc.timestamp()),
            event_url=event_url,
            edition=selected_edition,
            edition_emoji=selected_edition_emoji,
            room_state=room_snapshot.state,
            room_url=room_snapshot.url,
            edition_image_url=selected_edition_image_url,
            event_description=event_input.description,
        ),
        event_input.title,
    )


def _event_success_message(
    *,
    created_event: bool,
    linked_existing: bool,
    display_name: str,
    event_channel: discord.TextChannel,
    rsvp_message: discord.Message,
) -> str:
    if created_event:
        return (
            f"Created **{display_name}** and posted RSVP in "
            f"{event_channel.mention}: {rsvp_message.jump_url}"
        )
    if linked_existing:
        return (
            f"Posted RSVP for **{display_name}** in "
            f"{event_channel.mention}: {rsvp_message.jump_url}"
        )
    return (
        f"Posted RSVP for **{display_name}** in "
        f"{event_channel.mention}: {rsvp_message.jump_url}\n"
        f"Länka eller skapa ett Discord-event via **{SELECT_EVENT_BUTTON_LABEL}**."
    )


async def _prepare_event_command(
    *,
    interaction: discord.Interaction,
    settings_store: GuildSettingsStore,
    resolve_text_channel_fn: Callable[[discord.Guild, int], discord.TextChannel | None],
) -> tuple[discord.Guild, discord.TextChannel] | None:
    event_context = await resolve_event_command_context(interaction)
    if event_context is None:
        return None
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
        return None

    deferred = await defer_thinking_response(interaction)
    if not deferred:
        return None

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
        return None
    return guild, event_channel


async def _post_rsvp_for_resolved_event(
    *,
    interaction: discord.Interaction,
    guild: discord.Guild,
    event_channel: discord.TextChannel,
    event_object: discord.ScheduledEvent | None,
    event_input: EventInput,
    event_url: str,
    created_event: bool,
    edition: app_commands.Choice[str] | None,
    bot: commands.Bot,
    settings_store: GuildSettingsStore,
    controller: RsvpController,
    active_views: MutableMapping[int, EventMessageView],
) -> None:
    selected_edition = edition.value if edition is not None else "Custom"
    logger.info(
        "/event edition selection guild_id=%s choice_present=%s choice_name=%r "
        "choice_value=%r selected_edition=%r",
        guild.id,
        edition is not None,
        None if edition is None else edition.name,
        None if edition is None else edition.value,
        selected_edition,
    )
    selected_edition_emoji, selected_edition_image_url = await resolve_edition_media(
        guild=guild,
        edition=selected_edition,
    )
    logger.info(
        "/event edition media resolved guild_id=%s selected_edition=%r emoji=%r image_url=%r",
        guild.id,
        selected_edition,
        selected_edition_emoji,
        selected_edition_image_url,
    )
    room_snapshot = RoomSnapshot.from_url(event_input.room_url)
    view_state, display_name = _view_state_for_event(
        event_object=event_object,
        event_input=event_input,
        event_url=event_url,
        selected_edition=selected_edition,
        selected_edition_emoji=selected_edition_emoji,
        selected_edition_image_url=selected_edition_image_url,
        room_snapshot=room_snapshot,
    )

    view = EventMessageView(
        view_state=view_state,
        controller=controller,
        settings_store=settings_store,
    )
    companion_event_url = (
        event_url if event_object is not None and "/events/" in event_url else None
    )
    rsvp_message = await post_rsvp_message(
        interaction=interaction,
        event_channel=event_channel,
        view=view,
        event_url=companion_event_url,
        created_event=created_event,
    )
    if rsvp_message is None:
        return

    persistence_warning = bind_and_register_posted_view(
        view=view,
        message=rsvp_message,
        channel_id=event_channel.id,
        guild_id=guild.id,
        active_views=active_views,
        bot=bot,
    )
    await interaction.followup.send(
        _event_success_message(
            created_event=created_event,
            linked_existing=event_object is not None and not created_event,
            display_name=display_name,
            event_channel=event_channel,
            rsvp_message=rsvp_message,
        ),
        ephemeral=True,
    )
    if persistence_warning is not None:
        await interaction.followup.send(persistence_warning, ephemeral=True)


async def handle_event_create_command(
    *,
    interaction: discord.Interaction,
    title: str,
    description: str,
    event: str | None,
    edition: app_commands.Choice[str] | None,
    room_link: str | None,
    start_time: str | None,
    bot: commands.Bot,
    settings_store: GuildSettingsStore,
    controller: RsvpController,
    active_views: MutableMapping[int, EventMessageView],
    get_bot_member_fn: Callable[[discord.Guild, discord.ClientUser | None], discord.Member | None],
    resolve_text_channel_fn: Callable[[discord.Guild, int], discord.TextChannel | None],
) -> None:
    """`/event create` — post RSVP; optionally create or link a Discord event.

    Omit ``event`` to post with the companion placeholder and link later via
    **Koppla evenemang**. Pass the create-new sentinel to create a scheduled
    event, or an existing event id to link it.
    """
    prepared = await _prepare_event_command(
        interaction=interaction,
        settings_store=settings_store,
        resolve_text_channel_fn=resolve_text_channel_fn,
    )
    if prepared is None:
        return
    guild, event_channel = prepared

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

    event_object, created_event, event_url = await _resolve_create_command_event(
        interaction=interaction,
        guild=guild,
        selected_event_value=event,
        event_input=event_input,
        event_channel=event_channel,
        bot=bot,
        get_bot_member_fn=get_bot_member_fn,
    )
    if not event_url:
        return

    await _post_rsvp_for_resolved_event(
        interaction=interaction,
        guild=guild,
        event_channel=event_channel,
        event_object=event_object,
        event_input=event_input,
        event_url=event_url,
        created_event=created_event,
        edition=edition,
        bot=bot,
        settings_store=settings_store,
        controller=controller,
        active_views=active_views,
    )


async def handle_event_link_command(
    *,
    interaction: discord.Interaction,
    event: str,
    edition: app_commands.Choice[str] | None,
    room_link: str | None,
    bot: commands.Bot,
    settings_store: GuildSettingsStore,
    controller: RsvpController,
    active_views: MutableMapping[int, EventMessageView],
    get_bot_member_fn: Callable[[discord.Guild, discord.ClientUser | None], discord.Member | None],
    resolve_text_channel_fn: Callable[[discord.Guild, int], discord.TextChannel | None],
) -> None:
    """`/event link` — link an existing Discord scheduled event and post RSVP.

    Title and description are taken from the scheduled event itself.
    """
    prepared = await _prepare_event_command(
        interaction=interaction,
        settings_store=settings_store,
        resolve_text_channel_fn=resolve_text_channel_fn,
    )
    if prepared is None:
        return
    guild, event_channel = prepared

    event_object, event_url = await _link_existing_event_for_command(
        interaction=interaction,
        guild=guild,
        selected_event_value=event,
        event_channel=event_channel,
        bot=bot,
        get_bot_member_fn=get_bot_member_fn,
    )
    if event_object is None or not event_url:
        return

    try:
        event_input = _event_input_from_scheduled_event(
            event_object=event_object,
            room_link=room_link,
        )
    except ValueError as exc:
        await interaction.followup.send(str(exc), ephemeral=True)
        return

    await _post_rsvp_for_resolved_event(
        interaction=interaction,
        guild=guild,
        event_channel=event_channel,
        event_object=event_object,
        event_input=event_input,
        event_url=event_url,
        created_event=False,
        edition=edition,
        bot=bot,
        settings_store=settings_store,
        controller=controller,
        active_views=active_views,
    )
