from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv


@dataclass(frozen=True)
class DiscordConfig:
    token: str
    guild_id: int | None

def load_config() -> DiscordConfig:
    load_dotenv()
    return DiscordConfig(
        token=_parse_token(os.getenv("DISCORD_TOKEN")), 
        guild_id=_parse_guild_id(os.getenv("DISCORD_GUILD_ID")))

def _parse_token(raw_token: str | None) -> str:
    normalized_token = raw_token and raw_token.strip() or None
    if not normalized_token or normalized_token == "your_discord_bot_token_here":
        raise RuntimeError("Set DISCORD_TOKEN in .env or the process environment.")
    return normalized_token

def _parse_guild_id(raw_guild_id: str | None) -> int | None:
    try:
        normalized = raw_guild_id and raw_guild_id.strip() or None
        return normalized and int(normalized) or None
    except (TypeError, ValueError) as exc:
        raise RuntimeError("DISCORD_GUILD_ID must be an integer when set.") from exc


