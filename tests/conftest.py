"""Shared pytest fixtures for the Butler test suite."""

from __future__ import annotations

import os

import pytest

from tests.fakes import (
    FakeGuild,
    FakeInteraction,
    FakeMember,
    FakePermissions,
)

# `butler.app` loads Discord config at import time (it needs DISCORD_TOKEN). Tests never
# hit the network, so provide a dummy token before any test module imports the app. This
# also keeps the suite independent of a developer's local .env / config.toml.
os.environ.setdefault("DISCORD_TOKEN", "test-token-for-pytest")


@pytest.fixture
def guild() -> FakeGuild:
    return FakeGuild(guild_id=1)


@pytest.fixture
def admin_member() -> FakeMember:
    """A member with `Manage Server`, i.e. allowed to manage events/rooms."""
    return FakeMember(id=100, guild_permissions=FakePermissions(manage_guild=True))


@pytest.fixture
def plain_member() -> FakeMember:
    """A member with no elevated permissions."""
    return FakeMember(id=101, guild_permissions=FakePermissions())


@pytest.fixture
def interaction(guild: FakeGuild, plain_member: FakeMember) -> FakeInteraction:
    return FakeInteraction(guild=guild, user=plain_member)
