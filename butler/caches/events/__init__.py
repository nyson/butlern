"""Scheduled-event option cache package.

Public surface matches the former ``butler.caches.events`` module so existing
imports keep working:

    from butler.caches.events import warmup_connected_event_cache, ...
"""

from __future__ import annotations

from butler.caches.events.autocomplete import (
    autocomplete_existing_event,
    autocomplete_existing_or_create_event,
)
from butler.caches.events.gateway import (
    handle_gateway_scheduled_event_delete,
    handle_gateway_scheduled_event_upsert,
)
from butler.caches.events.listing import (
    fetch_scheduled_event_by_id,
    reusable_scheduled_events_for_guild,
)
from butler.caches.events.option_cache import (
    AUTOCOMPLETE_EVENT_CACHE,
    cache_connected_event_id,
    cache_reusable_events_for_guild,
    cached_connected_event_id,
    clear_connected_event_id,
    drop_cached_event_option,
    event_option_cache_is_fresh,
    invalidate_event_option_cache,
    reset_connected_event_cache,
    upsert_cached_event_option,
)
from butler.caches.events.recurrence import occurrence_start_utc_for_local_date
from butler.caches.events.resolve import (
    ExistingEventResolution,
    event_start_unix,
    parse_selected_event_id,
    resolve_cached_existing_event,
    resolve_existing_event_for_command,
    resolve_selected_existing_event,
)
from butler.caches.events.reusability import (
    event_choice_name,
    event_sort_key,
    is_reusable_scheduled_event,
)
from butler.caches.events.urls import build_event_url, build_preview_event_url
from butler.caches.events.warmup import warmup_connected_event_cache

__all__ = [
    "AUTOCOMPLETE_EVENT_CACHE",
    "ExistingEventResolution",
    "autocomplete_existing_event",
    "autocomplete_existing_or_create_event",
    "build_event_url",
    "build_preview_event_url",
    "cache_connected_event_id",
    "cache_reusable_events_for_guild",
    "cached_connected_event_id",
    "clear_connected_event_id",
    "drop_cached_event_option",
    "event_choice_name",
    "event_option_cache_is_fresh",
    "event_sort_key",
    "event_start_unix",
    "fetch_scheduled_event_by_id",
    "handle_gateway_scheduled_event_delete",
    "handle_gateway_scheduled_event_upsert",
    "invalidate_event_option_cache",
    "is_reusable_scheduled_event",
    "occurrence_start_utc_for_local_date",
    "parse_selected_event_id",
    "reset_connected_event_cache",
    "resolve_cached_existing_event",
    "resolve_existing_event_for_command",
    "resolve_selected_existing_event",
    "reusable_scheduled_events_for_guild",
    "upsert_cached_event_option",
    "warmup_connected_event_cache",
]
