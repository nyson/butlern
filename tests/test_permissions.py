"""Unit tests for the pure permission core (`butler.permissions`).

`can_manage_events` and `permission_denied_message` are pure and need no Discord objects.
"""

from __future__ import annotations

from butler.permissions import can_manage_events, permission_denied_message

# --- can_manage_events ------------------------------------------------------


def test_manage_guild_always_grants() -> None:
    assert can_manage_events(
        has_manage_guild=True, member_role_ids=set(), event_manager_role_id=None
    )
    assert can_manage_events(
        has_manage_guild=True, member_role_ids=set(), event_manager_role_id=42
    )


def test_no_role_configured_requires_manage_guild() -> None:
    assert not can_manage_events(
        has_manage_guild=False, member_role_ids={1, 2}, event_manager_role_id=None
    )


def test_configured_role_grants_only_when_member_has_it() -> None:
    assert can_manage_events(
        has_manage_guild=False, member_role_ids={1, 2, 3}, event_manager_role_id=3
    )
    assert not can_manage_events(
        has_manage_guild=False, member_role_ids={1, 2}, event_manager_role_id=3
    )


# --- permission_denied_message ----------------------------------------------


def test_denied_message_generic_when_no_role() -> None:
    assert (
        permission_denied_message(
            role_mention=None, without_role="generic", with_role_template="role {mention}"
        )
        == "generic"
    )


def test_denied_message_uses_role_template_when_resolved() -> None:
    assert (
        permission_denied_message(
            role_mention="<@&9>", without_role="generic", with_role_template="role {mention}"
        )
        == "role <@&9>"
    )
