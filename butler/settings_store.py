from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Final

SCHEMA_SQL: Final[str] = """
CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id INTEGER PRIMARY KEY,
    event_channel_id INTEGER,
    event_manager_role_id INTEGER
)
"""


@dataclass
class GuildSettingsStore:
    path: Path

    @classmethod
    def load(cls, path: Path) -> GuildSettingsStore:
        store = cls(path=path)
        store._ensure_schema()
        store._migrate_legacy_json_if_needed()
        return store

    def get_default_event_channel_id(self, guild_id: int) -> int | None:
        return self._get_value(guild_id=guild_id, column="event_channel_id")

    def set_default_event_channel_id(self, guild_id: int, channel_id: int) -> None:
        self._execute(
            """
            INSERT INTO guild_settings (guild_id, event_channel_id)
            VALUES (?, ?)
            ON CONFLICT(guild_id)
            DO UPDATE SET event_channel_id = excluded.event_channel_id
            """,
            (guild_id, channel_id),
        )

    def get_event_manager_role_id(self, guild_id: int) -> int | None:
        return self._get_value(guild_id=guild_id, column="event_manager_role_id")

    def set_event_manager_role_id(self, guild_id: int, role_id: int) -> None:
        self._execute(
            """
            INSERT INTO guild_settings (guild_id, event_manager_role_id)
            VALUES (?, ?)
            ON CONFLICT(guild_id)
            DO UPDATE SET event_manager_role_id = excluded.event_manager_role_id
            """,
            (guild_id, role_id),
        )

    def clear_event_manager_role_id(self, guild_id: int) -> None:
        self._execute(
            "UPDATE guild_settings SET event_manager_role_id = NULL WHERE guild_id = ?",
            (guild_id,),
        )
        self._execute(
            """
            DELETE FROM guild_settings
            WHERE guild_id = ?
              AND event_channel_id IS NULL
              AND event_manager_role_id IS NULL
            """,
            (guild_id,),
        )

    def _ensure_schema(self) -> None:
        self._execute(SCHEMA_SQL)

    def _migrate_legacy_json_if_needed(self) -> None:
        if self._has_any_settings():
            return
        legacy_path = self.path.with_name("guild_settings.json")
        if not legacy_path.exists() or legacy_path.is_dir():
            return
        try:
            raw_content = legacy_path.read_text(encoding="utf-8")
            parsed_content = json.loads(raw_content)
        except (OSError, json.JSONDecodeError):
            return
        for guild_id, event_channel_id, event_manager_role_id in _legacy_rows(parsed_content):
            self._execute(
                """
                INSERT INTO guild_settings (
                    guild_id,
                    event_channel_id,
                    event_manager_role_id
                )
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id)
                DO UPDATE SET
                    event_channel_id = excluded.event_channel_id,
                    event_manager_role_id = excluded.event_manager_role_id
                """,
                (guild_id, event_channel_id, event_manager_role_id),
            )

    def _has_any_settings(self) -> bool:
        try:
            with self._connect() as connection:
                row = connection.execute("SELECT 1 FROM guild_settings LIMIT 1").fetchone()
        except sqlite3.Error as exc:
            raise OSError(f"Failed to read settings from {self.path}.") from exc
        return row is not None

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self.path)

    def _execute(self, sql: str, params: tuple[object, ...] = ()) -> None:
        try:
            with self._connect() as connection:
                connection.execute(sql, params)
                connection.commit()
        except sqlite3.Error as exc:
            raise OSError(f"Failed to persist settings in {self.path}.") from exc

    def _get_value(self, *, guild_id: int, column: str) -> int | None:
        if column not in {"event_channel_id", "event_manager_role_id"}:
            return None
        try:
            with self._connect() as connection:
                row = connection.execute(
                    f"SELECT {column} FROM guild_settings WHERE guild_id = ?",
                    (guild_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise OSError(f"Failed to read settings from {self.path}.") from exc

        if row is None:
            return None
        value = row[0]
        if isinstance(value, int):
            return value
        return None


def _legacy_rows(parsed_content: object) -> list[tuple[int, int | None, int | None]]:
    if not isinstance(parsed_content, dict):
        return []
    rows: list[tuple[int, int | None, int | None]] = []
    for raw_guild_id, guild_settings in parsed_content.items():
        if not isinstance(raw_guild_id, str) or not isinstance(guild_settings, dict):
            continue
        try:
            guild_id = int(raw_guild_id)
        except ValueError:
            continue
        event_channel_id = guild_settings.get("event_channel_id")
        if not isinstance(event_channel_id, int):
            event_channel_id = None
        event_manager_role_id = guild_settings.get("event_manager_role_id")
        if not isinstance(event_manager_role_id, int):
            event_manager_role_id = None
        if event_channel_id is None and event_manager_role_id is None:
            continue
        rows.append((guild_id, event_channel_id, event_manager_role_id))
    return rows
