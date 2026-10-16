from __future__ import annotations

import json
from typing import Any, cast

from butler.domains.rsvp.types import RoomEntry, RoomSource, RoomState
from butler.rooms.types import BotcJoinSnapshot, BotcOccupancy, BotcPerson


def rooms_to_json(rooms: tuple[RoomEntry, ...]) -> str:
    payload = [room_to_dict(room) for room in rooms]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def rooms_from_json(raw: str | None) -> tuple[RoomEntry, ...]:
    if raw is None or not raw.strip():
        return ()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    if not isinstance(data, list):
        return ()
    rooms: list[RoomEntry] = []
    for item in data:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(item, dict):
            continue
        room = room_from_dict(cast(dict[str, Any], item))
        if room is not None:
            rooms.append(room)
    return tuple(rooms)


def room_to_dict(room: RoomEntry) -> dict[str, Any]:
    return {
        "room_id": room.room_id,
        "url": room.url,
        "state": room.state,
        "label": room.label,
        "source": room.source,
        "last_checked_unix": room.last_checked_unix,
        "closed_at_unix": room.closed_at_unix,
        "botc": None if room.botc is None else botc_to_dict(room.botc),
    }


def room_from_dict(data: dict[str, Any]) -> RoomEntry | None:
    room_id = data.get("room_id")
    url = data.get("url")
    state = data.get("state")
    if not isinstance(room_id, str) or not isinstance(url, str) or not isinstance(state, str):
        return None
    if state not in {"pending", "open", "closed"}:
        return None
    source_raw = data.get("source", "manual")
    source: RoomSource = "polled" if source_raw == "polled" else "manual"
    botc_raw = data.get("botc")
    botc = botc_from_dict(botc_raw) if isinstance(botc_raw, dict) else None
    return RoomEntry(
        room_id=room_id,
        url=url,
        state=cast(RoomState, state),
        label=data.get("label") if isinstance(data.get("label"), str) else None,
        source=source,
        last_checked_unix=_optional_int(data.get("last_checked_unix")),
        closed_at_unix=_optional_int(data.get("closed_at_unix")),
        botc=botc,
    )


def botc_to_dict(snapshot: BotcJoinSnapshot) -> dict[str, Any]:
    return {
        "code": snapshot.code,
        "occupancy": snapshot.occupancy,
        "is_private": snapshot.is_private,
        "open_seats": snapshot.open_seats,
        "chat_language": snapshot.chat_language,
        "edition": snapshot.edition,
        "phase": snapshot.phase,
        "storytellers": [person_to_dict(person) for person in snapshot.storytellers],
        "players": [person_to_dict(person) for person in snapshot.players],
        "player_count": snapshot.player_count,
    }


def botc_from_dict(data: dict[str, Any]) -> BotcJoinSnapshot | None:
    code = data.get("code")
    occupancy = data.get("occupancy")
    if not isinstance(code, str) or occupancy not in {"open", "closed", "unknown"}:
        return None
    storytellers = tuple(
        person
        for person in (
            person_from_dict(item)
            for item in data.get("storytellers", [])
            if isinstance(item, dict)
        )
        if person is not None
    )
    players = tuple(
        person
        for person in (
            person_from_dict(item)
            for item in data.get("players", [])
            if isinstance(item, dict)
        )
        if person is not None
    )
    return BotcJoinSnapshot(
        code=code,
        occupancy=cast(BotcOccupancy, occupancy),
        is_private=bool(data.get("is_private", False)),
        open_seats=_optional_int(data.get("open_seats")),
        chat_language=data.get("chat_language")
        if isinstance(data.get("chat_language"), str)
        else None,
        edition=data.get("edition") if isinstance(data.get("edition"), str) else None,
        phase=data.get("phase") if isinstance(data.get("phase"), str) else None,
        storytellers=storytellers,
        players=players,
        player_count=_optional_int(data.get("player_count")),
    )


def person_to_dict(person: BotcPerson) -> dict[str, Any]:
    return {
        "name": person.name,
        "external_id": person.external_id,
        "is_empty_seat": person.is_empty_seat,
    }


def person_from_dict(data: dict[str, Any]) -> BotcPerson | None:
    name = data.get("name")
    if not isinstance(name, str) or not name:
        return None
    external_id = data.get("external_id")
    return BotcPerson(
        name=name,
        external_id=external_id if isinstance(external_id, str) else None,
        is_empty_seat=bool(data.get("is_empty_seat", False)),
    )


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None
