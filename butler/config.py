from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DiscordConfig:
    token: str
    guild_id: int | None


def load_config(config_path: Path) -> DiscordConfig:
    if not config_path.exists():
        raise RuntimeError(
            f"{config_path.name} not found. Copy config.toml.example to "
            "config.toml and set your token."
        )

    try:
        raw_config = config_path.read_text(encoding="utf-8")
        parsed_config = tomllib.loads(raw_config)
    except tomllib.TOMLDecodeError as exc:
        raise RuntimeError(f"Invalid TOML in {config_path.name}: {exc}") from exc

    return parse_config(parsed_config)


def parse_config(config_data: object) -> DiscordConfig:
    if not isinstance(config_data, dict):
        raise RuntimeError("Invalid config format. Expected a top-level TOML table.")

    discord_config = config_data.get("discord")
    if not isinstance(discord_config, dict):
        raise RuntimeError("Missing [discord] section in config.toml.")

    token = _parse_token(discord_config.get("token"))
    guild_id = _parse_guild_id(discord_config.get("guild_id"))
    return DiscordConfig(token=token, guild_id=guild_id)


def _parse_token(raw_token: object) -> str:
    if not isinstance(raw_token, str):
        raise RuntimeError("Set [discord].token in config.toml to your real bot token.")
    normalized_token = raw_token.strip()
    if not normalized_token or normalized_token == "your_discord_bot_token_here":
        raise RuntimeError("Set [discord].token in config.toml to your real bot token.")
    return normalized_token


def _parse_guild_id(raw_guild_id: object) -> int | None:
    if raw_guild_id in (None, ""):
        return None
    if isinstance(raw_guild_id, bool):
        raise RuntimeError("[discord].guild_id must be an integer or omitted.")
    if isinstance(raw_guild_id, int):
        return raw_guild_id
    if not isinstance(raw_guild_id, str):
        raise RuntimeError("[discord].guild_id must be an integer or omitted.")
    try:
        return int(raw_guild_id)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("[discord].guild_id must be an integer or omitted.") from exc
