from __future__ import annotations

from pathlib import Path

from butler.settings_store import GuildSettings, GuildSettingsStore


def test_save_and_load_settings(tmp_path: Path) -> None:
    store = GuildSettingsStore.load(tmp_path / "settings.db")
    store.save_settings(
        42,
        GuildSettings(
            event_channel_id=10,
            event_announcement_channel_id=20,
            event_manager_role_id=30,
        ),
    )
    loaded = store.get_settings(42)
    assert loaded == GuildSettings(
        event_channel_id=10,
        event_announcement_channel_id=20,
        event_manager_role_id=30,
    )


def test_partial_update_preserves_other_fields(tmp_path: Path) -> None:
    store = GuildSettingsStore.load(tmp_path / "settings.db")
    store.save_settings(
        7,
        GuildSettings(
            event_channel_id=1,
            event_announcement_channel_id=2,
            event_manager_role_id=3,
        ),
    )
    store.set_default_event_channel_id(7, 99)
    loaded = store.get_settings(7)
    assert loaded.event_channel_id == 99
    assert loaded.event_announcement_channel_id == 2
    assert loaded.event_manager_role_id == 3


def test_clear_role_and_empty_row_cleanup(tmp_path: Path) -> None:
    store = GuildSettingsStore.load(tmp_path / "settings.db")
    store.set_event_manager_role_id(5, 11)
    assert store.get_event_manager_role_id(5) == 11
    store.clear_event_manager_role_id(5)
    assert store.get_settings(5) == GuildSettings()


def test_migrates_announcement_column_on_legacy_table(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    import sqlite3

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE guild_settings (
                guild_id INTEGER PRIMARY KEY,
                event_channel_id INTEGER,
                event_manager_role_id INTEGER
            )
            """
        )
        connection.execute(
            "INSERT INTO guild_settings (guild_id, event_channel_id, event_manager_role_id) "
            "VALUES (1, 10, 20)"
        )
        connection.commit()

    store = GuildSettingsStore.load(db_path)
    loaded = store.get_settings(1)
    assert loaded.event_channel_id == 10
    assert loaded.event_manager_role_id == 20
    assert loaded.event_announcement_channel_id is None

    store.set_event_announcement_channel_id(1, 33)
    assert store.get_event_announcement_channel_id(1) == 33
