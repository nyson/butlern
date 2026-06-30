from email import message
from pathlib import Path
from pydoc import describe
from re import M

from attr import dataclass
from typing_extensions import Final

import sqlite3

from butler.rsvp.rsvp_domain import RoomSnapshot
from butler.rsvp.types import RsvpResponse, ViewState


SCHEMA_SQL: Final[str] = """
CREATE TABLE IF NOT EXISTS rsvp_message (
    message_id INTEGER PRIMARY KEY,
    event_name TEXT NOT NULL,
    room_state TEXT NOT NULL,
    room_link TEXT NULL,
    event_url TEXT NOT NULL,
    edition TEXT NULL,
    start_unix INTEGER NOT NULL,

    description TEXT NULL,
    edition_emoji TEXT NULL,
    edition_image_url TEXT NULL
)

CREATE TABLE IF NOT EXISTS rsvp_response (
    message_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    role TEXT NOT NULL,
    arrival_time TEXT NULL,
    PRIMARY KEY (message_id, user_id),
    FOREIGN KEY (message_id) REFERENCES rsvp_message(message_id) ON DELETE CASCADE
)
"""
@dataclass(frozen=True)
class RsvpMessageStore:
    path: Path

    default_room_snapshot: RoomSnapshot = RoomSnapshot(state="pending", url=None)

    def _ensure_schema(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.executescript(SCHEMA_SQL)

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

    @classmethod
    def load(cls, path: Path) -> RsvpMessageStore:
        store = cls(path=path)
        store._ensure_schema()
        return store
    
    def create_rsvp_message(
        self,
        message_id: int,
        event_name: str,
        event_url: str,
        edition: str | None,
        start_unix: int,
        description: str,
    ) -> ViewState:
        view_state = ViewState(
                event_name=event_name,
                start_unix=start_unix,
                event_url=event_url,
                edition=edition,
                edition_emoji= None,
                room_state=self.default_room_snapshot.state,
                edition_image_url= None,
                room_url=self.default_room_snapshot.url,
                event_description=description,
            )

        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                INSERT INTO rsvp_message (
                    message_id, 
                    event_name, 
                    room_state, 
                    room_link, 
                    event_url, 
                    edition, 
                    start_unix, 
                    description, 
                    edition_emoji, 
                    edition_image_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (message_id, 
                 view_state.event_name, 
                 view_state.room_state, 
                 view_state.room_url,
                 view_state.event_url, 
                 view_state.edition, 
                 view_state.start_unix, 
                 view_state.event_description, 
                 view_state.edition_emoji, 
                 view_state.edition_image_url))

        return view_state
    
    def get_view_state(
            self,
            message_id: int
    ) -> ViewState | None:
        with self._connect() as conn:
            row = conn.execute(
                f"""SELECT
                    event_name,
                    description
                    room_state,
                    room_link,
                    event_url
                WHERE message_id = ?
                """,
                (message_id,)
            )
            return ViewState(
                event_name = row[0],
                description = row[1],
                room_state=row[2],
                room_url=row[3],
                start_unix=row[4]
                event_url=row[5
            )

    def update_rsvp_message(
            self,
            message_id: int, 
            st: ViewState
        ) -> ViewState | None:

        with self._connect() as conn: 
            conn.execute(
                f"""UPDATE rsvp_message
                SET 
                    event_name = ?
                    description = ?
                    room_state = ?
                    room_link = ?
                    event_url = ?
                    start_unix = ?
                WHERE message_id = ?
                """,
                (st.event_name, st.event_description, st.room_state, st.room_url, st.start_unix, message_id))     
            
            return self.get_view_state(message_id)
        raise NotImplementedError("update_rspv_response")
    
    def get_rsvp_response(
            self,
            message_id: int, 
            user_id: int
            ) -> RsvpResponse | None:
        try: 
            with self._connect() as connection:
                row = connection.execute(
                    f"""SELECT user_id, status, role, arrival_time 
                    FROM rsvp_response 
                    WHERE message_id = ?
                        AND user_id = ?
                    """,
                    (message_id, user_id)
                ).fetchone()

            return RsvpResponse(row[0], row[1], row[2], row[3])
                
        except sqlite3.Error as exc:
            raise OSError(f"Failed to read settings from {self.path}.") from exc

    def all_responses(
            self, 
            message_id: int
            ) -> list[RsvpResponse] | None :
        try: 
            with self._connect() as connection:
                rows = connection.execute(
                    f"""SELECT user_id, status, role, arrival_time 
                    FROM rsvp_response 
                    WHERE message_id = ?
                    """,
                    (message_id,)
                ).fetchone()

            return [ RsvpResponse(row[0], row[1], row[2], row[3]) for row in rows ]
                
        except sqlite3.Error as exc:
            raise OSError(f"Failed to read settings from {self.path}.") from exc

    def add_rsvp_response(
            self,
            message_id: int,
            rsvp: RsvpResponse
            ) -> RsvpResponse:
        self._execute(
            f"""INSERT INTO rsvp_message
            (user_id, message_id, status, role, arrival_time)
            VALUES
            (?, ?, ?, ?, ?)""",
            (rsvp.user, message_id, rsvp.status, rsvp.role, rsvp.arrival_time))
        
        inserted = self.get_rsvp_response(message_id, rsvp.user)
        assert(inserted is not None)
        return inserted 


    def update_rspv_response(
            self, 
            message_id: int, 
            rsvp: RsvpResponse
            ) -> RsvpResponse:
        
        self._execute(
            f"""UPDATE rsvp_message
            SET
                status = ?
                role = ?
                arrival_time = ?
            WHERE message_id = ?
                AND user_id = ?
            """,
            (rsvp.status, rsvp.role, rsvp.arrival_time, message_id, rsvp.user))
        
        updated = self.get_rsvp_response(message_id, rsvp.user)
        assert(updated is not None)
        return updated 



    