from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DiscordConfig:
    token: str
    guild_id: int | None


def load_config(config_path: Path) -> DiscordConfig:
    _load_env_file(config_path)
    token = _parse_token(os.getenv("DISCORD_TOKEN"))
    guild_id = _parse_guild_id(os.getenv("DISCORD_GUILD_ID"))
    return DiscordConfig(token=token, guild_id=guild_id)


def _load_env_file(config_path: Path) -> None:
    if not config_path.exists():
        return
    raw_lines = config_path.read_text(encoding="utf-8").splitlines()
    for raw_line in raw_lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        normalized_key = key.strip()
        if not normalized_key:
            continue
        normalized_value = value.strip()
        if (
            len(normalized_value) >= 2
            and normalized_value[0] == normalized_value[-1]
            and normalized_value[0] in {'"', "'"}
        ):
            normalized_value = normalized_value[1:-1]
        os.environ.setdefault(normalized_key, normalized_value)


def _parse_token(raw_token: str | None) -> str:
    if raw_token is None:
        raise RuntimeError("Set DISCORD_TOKEN in .env or the process environment.")
    normalized_token = raw_token.strip()
    if not normalized_token or normalized_token == "your_discord_bot_token_here":
        raise RuntimeError("Set DISCORD_TOKEN in .env or the process environment.")
    return normalized_token


def _parse_guild_id(raw_guild_id: str | None) -> int | None:
    if raw_guild_id is None:
        return None
    normalized = raw_guild_id.strip()
    if not normalized:
        return None
    try:
        return int(normalized)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("DISCORD_GUILD_ID must be an integer when set.") from exc
