"""Render snapshot shared by controller (producer) and view (consumer).

Kept outside ``view/`` so ``RsvpController`` does not import the Discord shell.
"""

from __future__ import annotations

from dataclasses import dataclass

from butler.domains.rsvp.domain import RsvpResponse
from butler.domains.rsvp.types import RoomState, ViewState


@dataclass(frozen=True)
class RsvpRenderSnapshot:
    """Immutable snapshot of everything needed to render an RSVP post."""

    view_state: ViewState
    responses: dict[int, RsvpResponse]

    @property
    def event_name(self) -> str:
        return self.view_state.event_name

    @property
    def event_description(self) -> str:
        return self.view_state.event_description

    @property
    def edition_emoji(self) -> str | None:
        return self.view_state.edition_emoji

    @property
    def edition_image_url(self) -> str | None:
        return self.view_state.edition_image_url

    @property
    def room_state(self) -> RoomState:
        return self.view_state.room_state

    @property
    def room_url(self) -> str | None:
        return self.view_state.room_url
