from __future__ import annotations

from collections.abc import MutableMapping

import discord
from discord.ext import commands

from butler.design import EMOJI_TO_STATUS, STORYTELLER_EMOJI
from butler.discord_helpers import fetch_message_from_channel
from butler.rsvp.rsvp_domain import status_from_emojis
from butler.rsvp.rsvp_store import RsvpMessageStore, StoredRsvpMessage
from butler.rsvp.rsvp_view import AvailabilityView
from butler.settings_store import GuildSettingsStore


def _view_from_stored_message(
    *,
    stored: StoredRsvpMessage,
    settings_store: GuildSettingsStore,
    view_store: RsvpMessageStore,
) -> AvailabilityView:
    return AvailabilityView(
        view_state=stored.view_state,
        settings_store=settings_store,
        view_store=view_store,
        message_id=stored.message_id,
        channel_id=stored.channel_id,
        guild_id=stored.guild_id,
        timeout=None,
    )


def _register_view(
    *,
    view: AvailabilityView,
    active_views: MutableMapping[int, AvailabilityView],
    bot: commands.Bot,
) -> None:
    message_id = view.message_id
    if message_id is None:
        return
    active_views[message_id] = view
    try:
        bot.add_view(view, message_id=message_id)
    except ValueError as exc:
        print(f"WARNING: failed to register persistent view for message {message_id}: {exc}")


def _drop_view(
    *,
    message_id: int,
    active_views: MutableMapping[int, AvailabilityView],
) -> None:
    active_views.pop(message_id, None)


def _delete_stored_message(
    *,
    message_id: int,
    view_store: RsvpMessageStore,
) -> None:
    try:
        view_store.delete_message(message_id)
    except OSError as exc:
        print(f"WARNING: failed to delete stale RSVP state for {message_id}: {exc}")


def bind_and_register_posted_view(
    *,
    view: AvailabilityView,
    message: discord.Message,
    channel_id: int,
    guild_id: int,
    active_views: MutableMapping[int, AvailabilityView],
    bot: commands.Bot,
) -> str | None:
    warning: str | None = None
    try:
        view.bind_message_context(
            message_id=message.id,
            channel_id=channel_id,
            guild_id=guild_id,
        )
    except OSError as exc:
        warning = (
            "Posted RSVP message, but failed to persist RSVP state. "
            "Restart recovery may not work until storage errors are fixed."
        )
        print(f"WARNING: failed to persist RSVP state for message {message.id}: {exc}")
    _register_view(view=view, active_views=active_views, bot=bot)
    return warning


async def resolve_active_view(
    *,
    message_id: int,
    channel_id: int,
    active_views: MutableMapping[int, AvailabilityView],
    bot: commands.Bot,
    settings_store: GuildSettingsStore,
    view_store: RsvpMessageStore,
) -> AvailabilityView | None:
    cached = active_views.get(message_id)
    if cached is not None:
        return cached

    try:
        stored = view_store.get_message(message_id)
    except OSError as exc:
        print(f"WARNING: failed to read RSVP state for {message_id}: {exc}")
        return None

    if stored is None:
        return None

    channel_candidates = [channel_id]
    if stored.channel_id != channel_id:
        channel_candidates.append(stored.channel_id)

    message: discord.Message | None = None
    for candidate_channel_id in channel_candidates:
        message = await fetch_message_from_channel(
            bot=bot,
            channel_id=candidate_channel_id,
            message_id=message_id,
        )
        if message is not None:
            break

    if message is None:
        _delete_stored_message(message_id=message_id, view_store=view_store)
        _drop_view(message_id=message_id, active_views=active_views)
        return None

    view = _view_from_stored_message(
        stored=stored,
        settings_store=settings_store,
        view_store=view_store,
    )
    _register_view(view=view, active_views=active_views, bot=bot)
    return view


async def hydrate_persistent_views(
    *,
    already_hydrated: bool,
    active_views: MutableMapping[int, AvailabilityView],
    bot: commands.Bot,
    settings_store: GuildSettingsStore,
    view_store: RsvpMessageStore,
) -> bool:
    if already_hydrated:
        return True

    try:
        stored_messages = view_store.list_messages()
    except OSError as exc:
        print(f"WARNING: failed to load persisted RSVP messages: {exc}")
        return True

    hydrated = 0
    cleaned = 0
    for stored in stored_messages:
        message = await fetch_message_from_channel(
            bot=bot,
            channel_id=stored.channel_id,
            message_id=stored.message_id,
        )
        if message is None:
            _delete_stored_message(message_id=stored.message_id, view_store=view_store)
            cleaned += 1
            continue
        _register_view(
            view=_view_from_stored_message(
                stored=stored,
                settings_store=settings_store,
                view_store=view_store,
            ),
            active_views=active_views,
            bot=bot,
        )
        hydrated += 1

    print(f"RSVP hydration complete: restored={hydrated}, cleaned={cleaned}.")
    return True


async def _user_has_reaction(
    reaction: discord.Reaction,
    *,
    user_id: int,
) -> bool:
    async for user in reaction.users():
        if user.id == user_id:
            return True
    return False


async def _user_reaction_emojis(
    *,
    message: discord.Message,
    user_id: int,
) -> list[str]:
    emojis: list[str] = []
    for reaction in message.reactions:
        if await _user_has_reaction(reaction, user_id=user_id):
            emojis.append(str(reaction.emoji))
    return emojis


async def edit_rsvp_message(
    *,
    message: discord.Message,
    view: AvailabilityView,
) -> None:
    try:
        await message.edit(
            content=await view.build_content(),
            embed=view.build_embed(),
            view=view,
        )
    except discord.HTTPException:
        return


async def sync_user_response_from_reactions(
    *,
    view: AvailabilityView,
    message: discord.Message,
    user_id: int,
) -> None:
    user_emojis = await _user_reaction_emojis(
        message=message,
        user_id=user_id,
    )
    resolved_status = status_from_emojis(user_emojis, EMOJI_TO_STATUS)
    if resolved_status is None:
        await view.remove_user_response(user_id)
        return
    await view.set_user_response(user_id=user_id, status=resolved_status)
    await view.set_storyteller_role(
        user_id=user_id,
        is_storyteller=STORYTELLER_EMOJI in user_emojis,
    )


async def reconcile_reaction_update(
    *,
    message_id: int,
    channel_id: int,
    user_id: int,
    active_views: MutableMapping[int, AvailabilityView],
    bot: commands.Bot,
    settings_store: GuildSettingsStore,
    view_store: RsvpMessageStore,
) -> None:
    view = await resolve_active_view(
        message_id=message_id,
        channel_id=channel_id,
        active_views=active_views,
        bot=bot,
        settings_store=settings_store,
        view_store=view_store,
    )
    if view is None:
        return

    message = await fetch_message_from_channel(
        bot=bot,
        channel_id=channel_id,
        message_id=message_id,
    )
    if message is None:
        _drop_view(message_id=message_id, active_views=active_views)
        _delete_stored_message(message_id=message_id, view_store=view_store)
        return

    await sync_user_response_from_reactions(
        view=view,
        message=message,
        user_id=user_id,
    )
    await edit_rsvp_message(message=message, view=view)
