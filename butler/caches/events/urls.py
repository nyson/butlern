from __future__ import annotations


def build_event_url(*, guild_id: int, event_id: int) -> str:
    return f"https://discord.com/events/{guild_id}/{event_id}"


def build_preview_event_url(*, guild_id: int, channel_id: int) -> str:
    return f"https://discord.com/channels/{guild_id}/{channel_id}"
