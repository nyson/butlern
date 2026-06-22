from typing import Literal

from attr import dataclass

RsvpRole = Literal["Player", "Storyteller"]
RsvpStatus = Literal["Available", "Maybe", "Cant"]
RoomState = Literal["pending", "open", "closed"]
# Logical room-action buttons, independent of which view renders them.
RoomButton = Literal["open_or_prompt", "close"]

@dataclass(frozen=True)
class ViewState:
    event_name: str
    start_unix: int
    event_url: str
    edition: str | None
    edition_emoji: str | None
    room_state: RoomState
    room_url: str | None
    edition_image_url: str | None
    event_manager_role_id: int | None
    event_description: str
