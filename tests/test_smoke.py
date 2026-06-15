"""Smoke tests that prove the harness works: sync collection, async mode, and the fakes.

Real coverage lands in the per-area test issues (event_logic, rsvp_domain, rendering,
settings_store, command handlers). This file only verifies the plumbing.
"""

from __future__ import annotations

from tests.fakes import FakeInteraction


def test_harness_collects_sync_tests() -> None:
    assert True


async def test_async_mode_is_enabled() -> None:
    # If asyncio_mode were not "auto", this coroutine test would be skipped/errored.
    assert True


async def test_interaction_fake_records_responses(interaction: FakeInteraction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    await interaction.response.send_message("hej", ephemeral=True)
    await interaction.followup.send("klar")

    assert interaction.response.deferred is True
    assert [message.content for message in interaction.all_messages] == ["hej", "klar"]
    assert interaction.all_messages[0].ephemeral is True
