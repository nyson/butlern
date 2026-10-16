from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

BotcOccupancy = Literal["open", "closed", "unknown"]


@dataclass(frozen=True)
class BotcPerson:
    name: str
    external_id: str | None = None
    is_empty_seat: bool = False


@dataclass(frozen=True)
class BotcJoinSnapshot:
    code: str
    occupancy: BotcOccupancy
    is_private: bool = False
    open_seats: int | None = None
    chat_language: str | None = None
    edition: str | None = None
    phase: str | None = None
    storytellers: tuple[BotcPerson, ...] = ()
    players: tuple[BotcPerson, ...] = ()
    player_count: int | None = None

    @property
    def filled_players(self) -> tuple[BotcPerson, ...]:
        return tuple(person for person in self.players if not person.is_empty_seat)
