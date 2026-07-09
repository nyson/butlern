from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from butler.rsvp.rsvp_domain import RsvpResponse
from butler.rsvp.types import RoomState, RsvpRole, RsvpStatus, ViewState

SCHEMA_SQL: Final[str] = """
CREATE TABLE IF NOT EXISTS rsvp_message (
    message_id INTEGER PRIMARY KEY,
    channel_id INTEGER NOT NULL,
    guild_id INTEGER NOT NULL,
    event_name TEXT NOT NULL,
    start_unix INTEGER NOT NULL,
    event_url TEXT NOT NULL,
    edition TEXT NULL,
    edition_emoji TEXT NULL,
    room_state TEXT NOT NULL,
    room_url TEXT NULL,
    edition_image_url TEXT NULL,
    event_description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rsvp_response (
    message_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    role TEXT NOT NULL,
    arrival_time TEXT NULL,
    PRIMARY KEY (message_id, user_id),
    FOREIGN KEY (message_id) REFERENCES rsvp_message(message_id) ON DELETE CASCADE
);
"""


@dataclass(frozen=True)
class StoredRsvpMessage:
    message_id: int
    channel_id: int
    guild_id: int
    view_state: ViewState


@dataclass(frozen=True)
class RsvpMessageStore:
    path: Path

    @classmethod
    def load(cls, path: Path) -> RsvpMessageStore:
        store = cls(path=path)
        store._ensure_schema()
        return store

    def upsert_message(
        self,
        *,
        message_id: int,
        channel_id: int,
        guild_id: int,
        view_state: ViewState,
    ) -> None:
        self._execute(
            """
            INSERT INTO rsvp_message (
                message_id,
                channel_id,
                guild_id,
                event_name,
                start_unix,
                event_url,
                edition,
                edition_emoji,
                room_state,
                room_url,
                edition_image_url,
                event_description
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(message_id)
            DO UPDATE SET
                channel_id = excluded.channel_id,
                guild_id = excluded.guild_id,
                event_name = excluded.event_name,
                start_unix = excluded.start_unix,
                event_url = excluded.event_url,
                edition = excluded.edition,
                edition_emoji = excluded.edition_emoji,
                room_state = excluded.room_state,
                room_url = excluded.room_url,
                edition_image_url = excluded.edition_image_url,
                event_description = excluded.event_description
            """,
            (
                message_id,
                channel_id,
                guild_id,
                view_state.event_name,
                view_state.start_unix,
                view_state.event_url,
                view_state.edition,
                view_state.edition_emoji,
                view_state.room_state,
                view_state.room_url,
                view_state.edition_image_url,
                view_state.event_description,
            ),
        )

    def get_message(self, message_id: int) -> StoredRsvpMessage | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT
                        message_id,
                        channel_id,
                        guild_id,
                        event_name,
                        start_unix,
                        event_url,
                        edition,
                        edition_emoji,
                        room_state,
                        room_url,
                        edition_image_url,
                        event_description
                    FROM rsvp_message
                    WHERE message_id = ?
                    """,
                    (message_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise OSError(f"Failed to read RSVP state from {self.path}.") from exc
        if row is None:
            return None
        return self._stored_message_from_row(row)

    def list_messages(self) -> list[StoredRsvpMessage]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT
                        message_id,
                        channel_id,
                        guild_id,
                        event_name,
                        start_unix,
                        event_url,
                        edition,
                        edition_emoji,
                        room_state,
                        room_url,
                        edition_image_url,
                        event_description
                    FROM rsvp_message
                    ORDER BY message_id
                    """
                ).fetchall()
        except sqlite3.Error as exc:
            raise OSError(f"Failed to read RSVP state from {self.path}.") from exc
        return [self._stored_message_from_row(row) for row in rows]

    def update_view_state(self, *, message_id: int, view_state: ViewState) -> None:
        self._execute(
            """
            UPDATE rsvp_message
            SET
                event_name = ?,
                start_unix = ?,
                event_url = ?,
                edition = ?,
                edition_emoji = ?,
                room_state = ?,
                room_url = ?,
                edition_image_url = ?,
                event_description = ?
            WHERE message_id = ?
            """,
            (
                view_state.event_name,
                view_state.start_unix,
                view_state.event_url,
                view_state.edition,
                view_state.edition_emoji,
                view_state.room_state,
                view_state.room_url,
                view_state.edition_image_url,
                view_state.event_description,
                message_id,
            ),
        )

    def delete_message(self, message_id: int) -> None:
        self._execute("DELETE FROM rsvp_message WHERE message_id = ?", (message_id,))

    def get_rsvp_response(self, message_id: int, user_id: int) -> RsvpResponse | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT status, role, arrival_time
                    FROM rsvp_response
                    WHERE message_id = ? AND user_id = ?
                    """,
                    (message_id, user_id),
                ).fetchone()
        except sqlite3.Error as exc:
            raise OSError(f"Failed to read RSVP state from {self.path}.") from exc
        if row is None:
            return None
        return self._response_from_row(row)

    def all_responses(self, message_id: int) -> dict[int, RsvpResponse]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT user_id, status, role, arrival_time
                    FROM rsvp_response
                    WHERE message_id = ?
                    """,
                    (message_id,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise OSError(f"Failed to read RSVP state from {self.path}.") from exc
        responses: dict[int, RsvpResponse] = {}
        for row in rows:
            user_id = row[0]
            if not isinstance(user_id, int):
                continue
            responses[user_id] = self._response_from_row((row[1], row[2], row[3]))
        return responses

    def upsert_rsvp_response(
        self,
        *,
        message_id: int,
        user_id: int,
        response: RsvpResponse,
    ) -> None:
        self._execute(
            """
            INSERT INTO rsvp_response (
                message_id,
                user_id,
                status,
                role,
                arrival_time
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(message_id, user_id)
            DO UPDATE SET
                status = excluded.status,
                role = excluded.role,
                arrival_time = excluded.arrival_time
            """,
            (
                message_id,
                user_id,
                response.status,
                response.role,
                response.arrival_time,
            ),
        )

    def remove_rsvp_response(self, *, message_id: int, user_id: int) -> None:
        self._execute(
            "DELETE FROM rsvp_response WHERE message_id = ? AND user_id = ?",
            (message_id, user_id),
        )

    def _ensure_schema(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connect() as connection:
                connection.executescript(SCHEMA_SQL)
                message_columns = self._table_columns(connection, "rsvp_message")
                required_message_columns = {
                    "message_id",
                    "channel_id",
                    "guild_id",
                    "event_name",
                    "start_unix",
                    "event_url",
                    "edition",
                    "edition_emoji",
                    "room_state",
                    "room_url",
                    "edition_image_url",
                    "event_description",
                }
                response_columns = self._table_columns(connection, "rsvp_response")
                required_response_columns = {
                    "message_id",
                    "user_id",
                    "status",
                    "role",
                    "arrival_time",
                }
                if not required_message_columns.issubset(message_columns) or (
                    not required_response_columns.issubset(response_columns)
                ):
                    connection.execute("DROP TABLE IF EXISTS rsvp_response")
                    connection.execute("DROP TABLE IF EXISTS rsvp_message")
                    connection.executescript(SCHEMA_SQL)
                connection.commit()
        except sqlite3.Error as exc:
            raise OSError(f"Failed to persist RSVP state in {self.path}.") from exc

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _execute(self, sql: str, params: tuple[object, ...] = ()) -> None:
        try:
            with self._connect() as connection:
                connection.execute(sql, params)
                connection.commit()
        except sqlite3.Error as exc:
            raise OSError(f"Failed to persist RSVP state in {self.path}.") from exc

    def _stored_message_from_row(self, row: sqlite3.Row | tuple[object, ...]) -> StoredRsvpMessage:
        values = cast(tuple[object, ...], tuple(row))
        message_id = self._required_int(values[0], "message_id")
        channel_id = self._required_int(values[1], "channel_id")
        guild_id = self._required_int(values[2], "guild_id")
        room_state = cast(RoomState, str(values[8]))
        view_state = ViewState(
            event_name=str(values[3]),
            start_unix=self._required_int(values[4], "start_unix"),
            event_url=str(values[5]),
            edition=self._optional_str(values[6]),
            edition_emoji=self._optional_str(values[7]),
            room_state=room_state,
            room_url=self._optional_str(values[9]),
            edition_image_url=self._optional_str(values[10]),
            event_description=str(values[11]),
        )
        return StoredRsvpMessage(
            message_id=message_id,
            channel_id=channel_id,
            guild_id=guild_id,
            view_state=view_state,
        )

    def _response_from_row(self, row: tuple[object, object, object | None]) -> RsvpResponse:
        status = cast(RsvpStatus, str(row[0]))
        role = cast(RsvpRole, str(row[1]))
        arrival_time = self._optional_str(row[2])
        return RsvpResponse(status=status, role=role, arrival_time=arrival_time)

    def _required_int(self, value: object, column: str) -> int:
        if isinstance(value, int):
            return value
        raise OSError(f"Invalid integer value for {column} in {self.path}.")

    def _optional_str(self, value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return str(value)

    def _table_columns(self, connection: sqlite3.Connection, table: str) -> set[str]:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
        columns: set[str] = set()
        for row in rows:
            if len(row) > 1 and isinstance(row[1], str):
                columns.add(row[1])
        return columns