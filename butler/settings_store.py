from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

GuildSettingsData = dict[str, dict[str, int]]


def sanitize_settings(raw_settings: object) -> GuildSettingsData:
    if not isinstance(raw_settings, dict):
        return {}

    sanitized: GuildSettingsData = {}
    for guild_id, value in raw_settings.items():
        if not isinstance(guild_id, str) or not isinstance(value, dict):
            continue
        guild_settings: dict[str, int] = {}
        channel_id = value.get("event_channel_id")
        if isinstance(channel_id, int):
            guild_settings["event_channel_id"] = channel_id
        event_manager_role_id = value.get("event_manager_role_id")
        if isinstance(event_manager_role_id, int):
            guild_settings["event_manager_role_id"] = event_manager_role_id
        if guild_settings:
            sanitized[guild_id] = guild_settings
    return sanitized


@dataclass
class GuildSettingsStore:
    path: Path
    _settings: GuildSettingsData = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> GuildSettingsStore:
        if not path.exists():
            return cls(path=path, _settings={})

        try:
            raw_content = path.read_text(encoding="utf-8")
            parsed_content = json.loads(raw_content)
        except (OSError, json.JSONDecodeError):
            parsed_content = {}

        return cls(path=path, _settings=sanitize_settings(parsed_content))

    def get_default_event_channel_id(self, guild_id: int) -> int | None:
        guild_data = self._settings.get(str(guild_id))
        if guild_data is None:
            return None
        return guild_data.get("event_channel_id")

    def set_default_event_channel_id(self, guild_id: int, channel_id: int) -> None:
        guild_key = str(guild_id)
        guild_data = self._settings.setdefault(guild_key, {})
        guild_data["event_channel_id"] = channel_id
        self._persist()

    def get_event_manager_role_id(self, guild_id: int) -> int | None:
        guild_data = self._settings.get(str(guild_id))
        if guild_data is None:
            return None
        return guild_data.get("event_manager_role_id")

    def set_event_manager_role_id(self, guild_id: int, role_id: int) -> None:
        guild_key = str(guild_id)
        guild_data = self._settings.setdefault(guild_key, {})
        guild_data["event_manager_role_id"] = role_id
        self._persist()

    def clear_event_manager_role_id(self, guild_id: int) -> None:
        guild_key = str(guild_id)
        guild_data = self._settings.get(guild_key)
        if guild_data is None:
            return
        guild_data.pop("event_manager_role_id", None)
        if not guild_data:
            self._settings.pop(guild_key, None)
        self._persist()

    def _persist(self) -> None:
        payload = json.dumps(self._settings, ensure_ascii=False, indent=2, sort_keys=True)
        self.path.write_text(payload, encoding="utf-8")
