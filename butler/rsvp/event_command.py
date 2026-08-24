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
from butler.event_logic import EventInput, resolve_event_input
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
    placeholder. Always posting it above the RSVP keeps late-link order correct:
    **Koppla evenemang** edits the same message instead of appending below.
    """
    try:
        companion_content = (
            event_url
            if event_url is not None and "/events/" in event_url
            else EVENT_CARD_PLACEHOLDER_MESSAGE
        )
        card_message = await event_channel.send(content=companion_content)
        view.set_event_card_message_id(card_message.id)
        return await event_channel.send(view=view)
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


async def _resolve_or_create_event(
    *,
    interaction: discord.Interaction,
    guild: discord.Guild,
    selected_event_value: str | None,
    event_input: EventInput,
    event_channel: discord.TextChannel,
    bot: commands.Bot,
    get_bot_member_fn: Callable[[discord.Guild, discord.ClientUser | None], discord.Member | None],
) -> tuple[discord.ScheduledEvent | None, bool, str]:
    """Return (event|None, created, event_url).

    ``selected_event_value=None`` (or empty) means post RSVP without a Discord
    scheduled event yet (placeholder channel URL). Create/link can still happen
    later via the button.
    """
    if selected_event_value is None or not selected_event_value.strip():
        if not await ensure_event_post_permissions(
            interaction=interaction,
            guild=guild,
            event_channel=event_channel,
            bot=bot,
            get_bot_member_fn=get_bot_member_fn,
        ):
            return None, False, ""
        placeholder = build_preview_event_url(
            guild_id=guild.id,
            channel_id=event_channel.id,
        )
        return None, False, placeholder

    if selected_event_value == CREATE_NEW_EVENT_CHOICE_VALUE:
        if not await ensure_event_creation_permissions(
            interaction=interaction,
            guild=guild,
            event_channel=event_channel,
            bot=bot,
            get_bot_member_fn=get_bot_member_fn,
        ):
            return None, False, ""
        created = await create_scheduled_event(
            interaction=interaction,
            guild=guild,
            event_input=event_input,
        )
        if created is None:
            return None, False, ""
        cache_connected_event_id(guild_id=guild.id, event_id=created.id)
        upsert_cached_event_option(guild_id=guild.id, event=created)
        logger.info(
            "/event created event %s for guild %s",
            created.id,
            guild.id,
        )
        return (
            created,
            True,
            build_event_url(guild_id=guild.id, event_id=created.id),
        )

    resolution = await resolve_existing_event_for_command(
        guild=guild,
        selected_event_value=selected_event_value,
    )
    if resolution.error_message is not None:
        await interaction.followup.send(resolution.error_message, ephemeral=True)
        return None, False, ""
    if resolution.event is None:
        # No usable cached/lookup event — post unlinked RSVP.
        if not await ensure_event_post_permissions(
            interaction=interaction,
            guild=guild,
            event_channel=event_channel,
            bot=bot,
            get_bot_member_fn=get_bot_member_fn,
        ):
            return None, False, ""
        placeholder = build_preview_event_url(
            guild_id=guild.id,
            channel_id=event_channel.id,
        )
        return None, False, placeholder

    if not await ensure_event_post_permissions(
        interaction=interaction,
        guild=guild,
        event_channel=event_channel,
        bot=bot,
        get_bot_member_fn=get_bot_member_fn,
    ):
        return None, False, ""

    event_object = resolution.event
    upsert_cached_event_option(guild_id=guild.id, event=event_object)
    logger.info(
        "/event linked existing event %s for guild %s (source=%s)",
        event_object.id,
        guild.id,
        resolution.source,
    )
    return (
        event_object,
        False,
        build_event_url(guild_id=guild.id, event_id=event_object.id),
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


async def handle_event_command(
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
    """Post an RSVP, optionally linking/creating a Discord scheduled event.

    ``event`` is optional cache-backed autocomplete (today/recurring-today +
    create-new). Omit it to post unlinked; link later via **Koppla evenemang**.
    """
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

    resolved = await _resolve_or_create_event(
        interaction=interaction,
        guild=guild,
        selected_event_value=event,
        event_input=event_input,
        event_channel=event_channel,
        bot=bot,
        get_bot_member_fn=get_bot_member_fn,
    )
    event_object, created_event, event_url = resolved
    if not event_url:
        # Permission/create failure already messaged the user.
        return

    selected_edition = edition.value if edition is not None else "Custom"
    selected_edition_emoji, selected_edition_image_url = resolve_edition_media(
        guild=guild,
        edition=selected_edition,
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
    # Real scheduled-event URLs unfurl as Discord's event card; otherwise a
    # placeholder occupies the companion slot above the RSVP.
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
            linked_existing=event_object is not None,
            display_name=display_name,
            event_channel=event_channel,
            rsvp_message=rsvp_message,
        ),
        ephemeral=True,
    )
    if persistence_warning is not None:
        await interaction.followup.send(persistence_warning, ephemeral=True)


