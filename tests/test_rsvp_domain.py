"""Unit tests for the pure RSVP domain (`butler.rsvp.rsvp_domain`).

Covers response bookkeeping, mention formatting, and the reaction-emoji resolver.
The room-state machine (`RoomSnapshot`, `visible_room_buttons`) is covered in
`test_room_state.py`.
"""

from __future__ import annotations

from butler.design import (
    AVAILABLE_EMOJI,
    EMOJI_TO_STATUS,
    LATER_EMOJI,
    MAYBE_EMOJI,
    STORYTELLER_EMOJI,
)
from butler.rsvp.rsvp_domain import (
    RsvpResponse,
    mentions_for_status,
    status_count,
    status_from_emoji,
    status_from_emojis,
    with_updated_response,
)

# --- with_updated_response --------------------------------------------------


def test_with_updated_response_does_not_mutate_input() -> None:
    original: dict[int, RsvpResponse] = {}
    updated = with_updated_response(original, user_id=1, role="Player", status="Available")
    assert original == {}
    assert updated[1] == RsvpResponse(status="Available")


def test_with_updated_response_overwrites_existing_user() -> None:
    responses = with_updated_response({}, user_id=1, role="Player", status="Available")
    responses = with_updated_response(responses, user_id=1, role="Player", status="Maybe")
    assert responses[1].status == "Maybe"
    assert len(responses) == 1


def test_with_updated_response_stores_arrival_time() -> None:
    responses = with_updated_response(
        {},
        user_id=1,
        status="Later",
        role="Player",
        arrival_time="20:30")
    assert responses[1] == RsvpResponse(status="Later", arrival_time="20:30")


# --- status_count -----------------------------------------------------------


def test_status_count_counts_only_matching() -> None:
    responses = {
        1: RsvpResponse(status="Available"),
        2: RsvpResponse(status="Available"),
        3: RsvpResponse(status="Maybe"),
    }
    assert status_count(responses, "Available") == 2
    assert status_count(responses, "Maybe") == 1
    assert status_count(responses, "Later") == 0


# --- mentions_for_status ----------------------------------------------------


def test_mentions_for_status_empty_is_none() -> None:
    assert mentions_for_status({}, "Available") is None


def test_mentions_for_status_later_includes_arrival_time() -> None:
    responses = {7: RsvpResponse(status="Later", arrival_time="21:00")}
    assert mentions_for_status(responses, "Later") == "<@7> (21:00)"


def test_mentions_for_status_other_status_is_plain_mention() -> None:
    responses = {7: RsvpResponse(status="Available")}
    assert mentions_for_status(responses, "Available") == "<@7>"


def test_mentions_for_status_later_without_arrival_time_is_plain() -> None:
    responses = {7: RsvpResponse(status="Later")}
    assert mentions_for_status(responses, "Later") == "<@7>"


def test_mentions_for_status_truncates_past_fifteen() -> None:
    responses = {uid: RsvpResponse(status="Available") for uid in range(1, 17)}  # 16 users
    result = mentions_for_status(responses, "Available")
    assert result is not None
    assert result.endswith("(+1 more)")
    assert result.count("<@") == 15


# --- status_from_emoji / status_from_emojis ---------------------------------


def test_status_from_emoji_maps_each_emoji() -> None:
    assert status_from_emoji(AVAILABLE_EMOJI, EMOJI_TO_STATUS) == "Available"
    assert status_from_emoji(MAYBE_EMOJI, EMOJI_TO_STATUS) == "Maybe"
    assert status_from_emoji(LATER_EMOJI, EMOJI_TO_STATUS) == "Later"


def test_status_from_emoji_unknown_is_available() -> None:
    assert status_from_emoji("🎲", EMOJI_TO_STATUS) == "Available"


# def test_status_from_emojis_empty_is_none() -> None:
#     assert status_from_emojis([], EMOJI_TO_STATUS) is None


def test_status_from_emojis_precedence_maybe_wins() -> None:
    emojis = [LATER_EMOJI, MAYBE_EMOJI, STORYTELLER_EMOJI, AVAILABLE_EMOJI]
    assert status_from_emojis(emojis, EMOJI_TO_STATUS) == "Maybe"


def test_status_from_emojis_precedence_later_over_storyteller() -> None:
    assert status_from_emojis([STORYTELLER_EMOJI, LATER_EMOJI], EMOJI_TO_STATUS) == "Later"


# def test_status_from_emojis_precedence_storyteller_over_available() -> None:
#     emojis = [AVAILABLE_EMOJI, STORYTELLER_EMOJI]
#     assert status_from_emojis(emojis, EMOJI_TO_STATUS) == "Storyteller"


def test_status_from_emojis_unknown_counts_as_available() -> None:
    assert status_from_emojis(["🎲"], EMOJI_TO_STATUS) == "Available"
