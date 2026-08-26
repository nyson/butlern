from __future__ import annotations

from collections.abc import Sequence


def format_permissions(permission_names: Sequence[str]) -> str:
    return ", ".join(f"`{name}`" for name in permission_names)


def can_manage_events(
    *,
    has_manage_guild: bool,
    member_role_ids: set[int],
    event_manager_role_id: int | None,
) -> bool:
    """Whether a member may create events and open/close rooms.

    Pure core: `Manage Server` always grants it; otherwise the member must hold the
    configured event-manager role (when one is configured at all).
    """
    if has_manage_guild:
        return True
    if event_manager_role_id is None:
        return False
    return event_manager_role_id in member_role_ids


def permission_denied_message(
    *,
    role_mention: str | None,
    without_role: str,
    with_role_template: str,
) -> str:
    """Pure: mention the configured role if one resolved, otherwise the generic message.

    `with_role_template` must contain a `{mention}` placeholder.
    """
    if role_mention is None:
        return without_role
    return with_role_template.format(mention=role_mention)
