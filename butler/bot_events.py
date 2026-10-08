from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass

import discord
from discord.ext import commands

import butler.rsvp.runtime as rsvp_runtime
from butler.caches.events import (
    handle_gateway_scheduled_event_delete,
    handle_gateway_scheduled_event_upsert,
    warmup_connected_event_cache,
)
from butler.config import DiscordConfig
from butler.domains.rsvp.store import RsvpMessageStore
from butler.jobs.runtime import (
    ButlerScheduler,
    DailyEventCacheJobHandle,
    set_runtime_scheduler,
)
from butler.permissions import guild_sync_access_message
from butler.rsvp.controller import RsvpController
from butler.rsvp.view.event_message_view import EventMessageView
from butler.settings_store import GuildSettingsStore

logger = logging.getLogger(__name__)


@dataclass
class BotEventState:
    rsvp_views_hydrated: bool = False
    event_cache_boot_hydrated: bool = False
    commands_synced: bool = False
    event_cache_daily_sync_started: bool = False


@dataclass(frozen=True)
class BotEventDependencies:
    get_runtime_bot_fn: Callable[[], commands.Bot]
    config: DiscordConfig
    is_force_guild_sync_fn: Callable[[], bool]
    get_active_views_fn: Callable[[], MutableMapping[int, EventMessageView]]
    get_settings_store_fn: Callable[[], GuildSettingsStore]
    get_view_store_fn: Callable[[], RsvpMessageStore]
    get_controller_fn: Callable[[], RsvpController]
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
    on_scheduled_event_create: Callable[[discord.ScheduledEvent], Awaitable[None]]
    on_scheduled_event_delete: Callable[[discord.ScheduledEvent], Awaitable[None]]
    on_scheduled_event_update: Callable[
        [discord.ScheduledEvent, discord.ScheduledEvent],
        Awaitable[None],
    ]
    setup_hook: Callable[[], Awaitable[None]]
    daily_event_cache_sync: DailyEventCacheJobHandle
    scheduler: ButlerScheduler


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
        logger.warning(guidance)
        logger.warning("Falling back to global command sync.")
        await runtime_bot.tree.sync()
        logger.debug("Synced global commands after missing guild access.")
        return
    logger.debug("Synced commands to guild %s.", guild_id)


async def _sync_commands_on_startup(*, deps: BotEventDependencies) -> None:
    runtime_bot = deps.get_runtime_bot_fn()

    if deps.is_force_guild_sync_fn():
        if deps.config.guild_id is None:
            raise RuntimeError(
                "butler-dev requires DISCORD_GUILD_ID in .env or the environment."
            )
        await _sync_to_guild(
            runtime_bot=runtime_bot,
            guild_id=deps.config.guild_id,
            strict=True,
        )
        logger.debug("Forced dev mode command sync is active.")
        return

    # Guild sync is immediate; global can take up to ~1h. Prefer every connected
    # guild so multi-server deploys don't leave non-DISCORD_GUILD_ID servers on
    # the previous command tree until global propagation finishes.
    guild_ids: list[int] = []
    seen: set[int] = set()
    if deps.config.guild_id is not None:
        guild_ids.append(deps.config.guild_id)
        seen.add(deps.config.guild_id)
    for guild in runtime_bot.guilds:
        if guild.id in seen:
            continue
        guild_ids.append(guild.id)
        seen.add(guild.id)

    for guild_id in guild_ids:
        await _sync_to_guild(
            runtime_bot=runtime_bot,
            guild_id=guild_id,
            strict=False,
        )

    await runtime_bot.tree.sync()
    logger.debug("Synced global commands.")


async def _handle_on_ready(
    *,
    deps: BotEventDependencies,
    state: BotEventState,
    scheduler: ButlerScheduler,
    daily_event_cache_sync: DailyEventCacheJobHandle,
) -> None:
    runtime_bot = deps.get_runtime_bot_fn()

    # RSVP hydrate is a boot job (store -> persistent views); keep before cache warm.
    state.rsvp_views_hydrated = await rsvp_runtime.hydrate_persistent_views(
        already_hydrated=state.rsvp_views_hydrated,
        active_views=deps.get_active_views_fn(),
        bot=runtime_bot,
        settings_store=deps.get_settings_store_fn(),
        view_store=deps.get_view_store_fn(),
        controller=deps.get_controller_fn(),
    )

    # Prefer warming the event cache before slash sync so autocomplete is hot,
    # but never let cache failures block command registration.
    if not state.event_cache_boot_hydrated:
        logger.debug("Boot scheduled-event cache hydrate starting.")
        try:
            await warmup_connected_event_cache(
                guilds=list(runtime_bot.guilds),
                force=True,
            )
            state.event_cache_boot_hydrated = True
            logger.debug("Boot scheduled-event cache hydrate finished.")
        except Exception:
            logger.exception(
                "Boot scheduled-event cache hydrate failed; continuing with slash sync."
            )

    if not state.commands_synced:
        logger.debug("Registering slash commands.")
        try:
            await _sync_commands_on_startup(deps=deps)
            state.commands_synced = True
            logger.debug("Slash command registration complete.")
        except Exception:
            # Leave commands_synced False so a later on_ready reconnect can retry.
            logger.exception("Slash command registration failed; will retry on next ready.")
            raise

    if not state.event_cache_daily_sync_started:
        daily_event_cache_sync.start()
        state.event_cache_daily_sync_started = True
        logger.debug("APScheduler event-cache jobs active running=%s", scheduler.running)

    if runtime_bot.user is not None:
        logger.info(
            "Butler ready as %s (ID: %s); rsvp_hydrated=%s event_cache=%s "
            "commands_synced=%s scheduler=%s",
            runtime_bot.user,
            runtime_bot.user.id,
            state.rsvp_views_hydrated,
            state.event_cache_boot_hydrated,
            state.commands_synced,
            scheduler.running,
        )


async def _handle_on_guild_join(*, deps: BotEventDependencies, guild: discord.Guild) -> None:
    runtime_bot = deps.get_runtime_bot_fn()
    try:
        await warmup_connected_event_cache(guilds=[guild], force=True)
    except Exception:
        logger.exception(
            "Event cache warmup failed on guild join guild=%s; continuing onboarding.",
            guild.id,
        )

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


async def _handle_setup_hook(*, deps: BotEventDependencies) -> None:
    _ = deps
    logger.debug(
        "setup_hook complete; slash commands will register after event-cache hydrate."
    )


def register_bot_events(
    *,
    register_bot: commands.Bot,
    get_runtime_bot_fn: Callable[[], commands.Bot],
    config: DiscordConfig,
    is_force_guild_sync_fn: Callable[[], bool],
    state: BotEventState,
    get_active_views_fn: Callable[[], MutableMapping[int, EventMessageView]],
    get_settings_store_fn: Callable[[], GuildSettingsStore],
    get_view_store_fn: Callable[[], RsvpMessageStore],
    get_controller_fn: Callable[[], RsvpController],
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
        get_controller_fn=get_controller_fn,
        get_bot_member_fn=get_bot_member_fn,
        find_onboarding_channel_fn=find_onboarding_channel_fn,
        onboarding_message=onboarding_message,
    )

    scheduler = ButlerScheduler(get_bot=deps.get_runtime_bot_fn)
    set_runtime_scheduler(scheduler)
    daily_event_cache_sync = DailyEventCacheJobHandle(scheduler)

    async def on_ready() -> None:
        await _handle_on_ready(
            deps=deps,
            state=state,
            scheduler=scheduler,
            daily_event_cache_sync=daily_event_cache_sync,
        )

    async def on_guild_join(guild: discord.Guild) -> None:
        await _handle_on_guild_join(deps=deps, guild=guild)

    async def on_scheduled_event_create(event: discord.ScheduledEvent) -> None:
        logger.info(
            "Gateway GUILD_SCHEDULED_EVENT_CREATE event_id=%s name=%r",
            event.id,
            event.name,
        )
        handle_gateway_scheduled_event_upsert(event, action="create")

    async def on_scheduled_event_delete(event: discord.ScheduledEvent) -> None:
        logger.info(
            "Gateway GUILD_SCHEDULED_EVENT_DELETE event_id=%s name=%r",
            event.id,
            event.name,
        )
        handle_gateway_scheduled_event_delete(event)

    async def on_scheduled_event_update(
        before: discord.ScheduledEvent,
        after: discord.ScheduledEvent,
    ) -> None:
        logger.info(
            "Gateway GUILD_SCHEDULED_EVENT_UPDATE event_id=%s name=%r "
            "status=%s->%s",
            after.id,
            after.name,
            before.status.name,
            after.status.name,
        )
        handle_gateway_scheduled_event_upsert(after, action="update")

    async def setup_hook() -> None:
        await _handle_setup_hook(deps=deps)

    register_bot.event(on_ready)
    register_bot.event(on_guild_join)
    register_bot.event(on_scheduled_event_create)
    register_bot.event(on_scheduled_event_delete)
    register_bot.event(on_scheduled_event_update)
    register_bot.event(setup_hook)

    return RegisteredBotEvents(
        on_ready=on_ready,
        on_guild_join=on_guild_join,
        on_scheduled_event_create=on_scheduled_event_create,
        on_scheduled_event_delete=on_scheduled_event_delete,
        on_scheduled_event_update=on_scheduled_event_update,
        setup_hook=setup_hook,
        daily_event_cache_sync=daily_event_cache_sync,
        scheduler=scheduler,
    )
