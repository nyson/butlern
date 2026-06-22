from typing import Literal

RsvpRole = Literal["Player", "Storyteller"]
RsvpStatus = Literal["Available", "Maybe", "Cant"]
RoomState = Literal["pending", "open", "closed"]
# Logical room-action buttons, independent of which view renders them.
RoomButton = Literal["open_or_prompt", "close"]
