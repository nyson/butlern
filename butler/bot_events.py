from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass

import discord
from discord.ext import commands

import butler.rsvp.runtime as rsvp_runtime
from butler.config import DiscordConfig
from butler.permissions import guild_sync_access_message
from butler.rsvp.rsvp_store import RsvpMessageStore
from butler.rsvp.rsvp_view import AvailabilityView
from butler.settings_store import GuildSettingsStore


@dataclass
class BotEventState:
    rsvp_views_hydrated: bool = False

@dataclass(frozen=True)
class BotEventDependencies:
    get_runtime_bot_fn: Callable[[], commands.Bot]
    config: DiscordConfig
    is_force_guild_sync_fn: Callable[[], bool]
    get_active_views_fn: Callable[[], MutableMapping[int, AvailabilityView]]
    get_settings_store_fn: Callable[[], GuildSettingsStore]
    get_view_store_fn: Callable[[], RsvpMessageStore]
    get_bot_member_fn: Callable[[discord.Guild, discord.ClientUser | None], discord.Member | None]
    find_onboarding_channel_fn: Callable[
        [discord.Guild, discord.Member],
        discord.TextChannel | None,
    ]
    onboarding_message: str


@dataclass(frozen=True)
class RegisteredBotEvents:
    on_ready: Callable[[], Awaitable[None]]
    on_guild_join: Callable[[discord.Guild], Awaitable[None]]
    on_raw_reaction_add: Callable[[discord.RawReactionActionEvent], Awaitable[None]]
    on_raw_reaction_remove: Callable[[discord.RawReactionActionEvent], Awaitable[None]]
    setup_hook: Callable[[], Awaitable[None]]

async def _sync_to_guild(
    *,
    runtime_bot: commands.Bot,
    guild_id: int,
    strict: bool,
) -> None:
    guild = discord.Object(id=guild_id)
    runtime_bot.tree.copy_global_to(guild=guild)
    try:
        await runtime_bot.tree.sync(guild=guild)
    except discord.Forbidden as exc:
        guidance = guild_sync_access_message(guild_id)
        if strict:
            raise RuntimeError(guidance) from exc
        print(f"WARNING: {guidance}")
        print("Falling back to global command sync.")
        await runtime_bot.tree.sync()
        print("Synced global commands.")
        return
    print(f"Synced commands to guild {guild_id}.")


async def _sync_commands_on_startup(*, deps: BotEventDependencies) -> None:
    if deps.is_force_guild_sync_fn():
        if deps.config.guild_id is None:
            raise RuntimeError(
                "butler-dev requires DISCORD_GUILD_ID in .env or the environment."
            )
        await _sync_to_guild(
            runtime_bot=deps.get_runtime_bot_fn(),
            guild_id=deps.config.guild_id,
            strict=True,
        )
        print("Forced dev mode command sync is active.")
        return

    if deps.config.guild_id is not None:
        await _sync_to_guild(
            runtime_bot=deps.get_runtime_bot_fn(),
            guild_id=deps.config.guild_id,
            strict=False,
        )

    await deps.get_runtime_bot_fn().tree.sync()
    print("Synced global commands.")


async def _reconcile_rsvp_reaction_update(
    *,
    deps: BotEventDependencies,
    message_id: int,
    channel_id: int,
    user_id: int,
) -> None:
    runtime_bot = deps.get_runtime_bot_fn()
    await rsvp_runtime.reconcile_reaction_update(
        message_id=message_id,
        channel_id=channel_id,
        user_id=user_id,
        active_views=deps.get_active_views_fn(),
        bot=runtime_bot,
        settings_store=deps.get_settings_store_fn(),
        view_store=deps.get_view_store_fn(),
    )


async def _handle_on_ready(*, deps: BotEventDependencies, state: BotEventState) -> None:
    runtime_bot = deps.get_runtime_bot_fn()
    if runtime_bot.user is not None:
        print(f"Logged in as {runtime_bot.user} (ID: {runtime_bot.user.id})")
    state.rsvp_views_hydrated = await rsvp_runtime.hydrate_persistent_views(
        already_hydrated=state.rsvp_views_hydrated,
        active_views=deps.get_active_views_fn(),
        bot=runtime_bot,
        settings_store=deps.get_settings_store_fn(),
        view_store=deps.get_view_store_fn(),
    )


async def _handle_on_guild_join(*, deps: BotEventDependencies, guild: discord.Guild) -> None:
    runtime_bot = deps.get_runtime_bot_fn()
    bot_member = deps.get_bot_member_fn(guild, runtime_bot.user)
    if bot_member is None:
        return

    onboarding_channel = deps.find_onboarding_channel_fn(guild, bot_member)
    if onboarding_channel is None:
        return

    try:
        await onboarding_channel.send(deps.onboarding_message)
    except discord.HTTPException:
        return


async def _handle_reaction_event(
    *,
    deps: BotEventDependencies,
    payload: discord.RawReactionActionEvent,
) -> None:
    runtime_bot = deps.get_runtime_bot_fn()
    if runtime_bot.user is not None and payload.user_id == runtime_bot.user.id:
        return
    await _reconcile_rsvp_reaction_update(
        deps=deps,
        message_id=payload.message_id,
        channel_id=payload.channel_id,
        user_id=payload.user_id,
    )


async def _handle_setup_hook(*, deps: BotEventDependencies) -> None:
    runtime_bot = deps.get_runtime_bot_fn()
    if not deps.is_force_guild_sync_fn():
        runtime_bot.tree.remove_command(
            "previeweventdesign",
            type=discord.AppCommandType.chat_input,
        )
    await _sync_commands_on_startup(deps=deps)



def register_bot_events(
    *,
    register_bot: commands.Bot,
    get_runtime_bot_fn: Callable[[], commands.Bot],
    config: DiscordConfig,
    is_force_guild_sync_fn: Callable[[], bool],
    state: BotEventState,
    get_active_views_fn: Callable[[], MutableMapping[int, AvailabilityView]],
    get_settings_store_fn: Callable[[], GuildSettingsStore],
    get_view_store_fn: Callable[[], RsvpMessageStore],
    get_bot_member_fn: Callable[[discord.Guild, discord.ClientUser | None], discord.Member | None],
    find_onboarding_channel_fn: Callable[
        [discord.Guild, discord.Member],
        discord.TextChannel | None,
    ],
    onboarding_message: str,
) -> RegisteredBotEvents:
    deps = BotEventDependencies(
        get_runtime_bot_fn=get_runtime_bot_fn,
        config=config,
        is_force_guild_sync_fn=is_force_guild_sync_fn,
        get_active_views_fn=get_active_views_fn,
        get_settings_store_fn=get_settings_store_fn,
        get_view_store_fn=get_view_store_fn,
        get_bot_member_fn=get_bot_member_fn,
        find_onboarding_channel_fn=find_onboarding_channel_fn,
        onboarding_message=onboarding_message,
    )

    async def on_ready() -> None:
        await _handle_on_ready(deps=deps, state=state)

    async def on_guild_join(guild: discord.Guild) -> None:
        await _handle_on_guild_join(deps=deps, guild=guild)

    async def on_raw_reaction_add(payload: discord.RawReactionActionEvent) -> None:
        await _handle_reaction_event(deps=deps, payload=payload)

    async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent) -> None:
        await _handle_reaction_event(deps=deps, payload=payload)

    async def setup_hook() -> None:
        await _handle_setup_hook(deps=deps)

    register_bot.event(on_ready)
    register_bot.event(on_guild_join)
    register_bot.event(on_raw_reaction_add)
    register_bot.event(on_raw_reaction_remove)
    register_bot.event(setup_hook)

    return RegisteredBotEvents(
        on_ready=on_ready,
        on_guild_join=on_guild_join,
        on_raw_reaction_add=on_raw_reaction_add,
        on_raw_reaction_remove=on_raw_reaction_remove,
        setup_hook=setup_hook,
    )
