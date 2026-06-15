"""Lightweight, dependency-free fakes for the Discord objects the imperative shell touches.

These let the shell (`butler.app`, `butler.rsvp.rsvp_view`) be unit-tested without a live
gateway connection. They deliberately do **not** subclass `discord.py` types: subclassing the
real classes drags in heavy state and trips strict typing. Instead each fake exposes only the
attributes/methods the bot actually reads, and records outgoing calls so tests can assert on them.

The functional-core tests (event_logic, rsvp_domain, rendering) should need none of this.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SentMessage:
    """A message captured by a fake send/response surface."""

    content: str | None = None
    ephemeral: bool = False
    extras: dict[str, object] = field(default_factory=dict)


class FakePermissions:
    """Permission set where every flag defaults to ``False`` unless explicitly enabled.

    Covers both guild-level (``manage_guild``, ``create_events``, ``use_application_commands``)
    and channel-level (``view_channel``, ``send_messages``, ``embed_links``) permissions.
    """

    def __init__(self, **flags: bool) -> None:
        self._flags = flags

    def __getattr__(self, name: str) -> bool:
        # Dunder lookups must not be treated as permission flags.
        if name.startswith("__"):
            raise AttributeError(name)
        return self._flags.get(name, False)


@dataclass
class FakeRole:
    id: int
    mention: str = ""

    def __post_init__(self) -> None:
        if not self.mention:
            self.mention = f"<@&{self.id}>"


@dataclass
class FakeMember:
    id: int
    guild_permissions: FakePermissions = field(default_factory=FakePermissions)
    roles: list[FakeRole] = field(default_factory=list)


class FakeTextChannel:
    def __init__(
        self,
        *,
        channel_id: int,
        guild: FakeGuild | None = None,
        permissions: FakePermissions | None = None,
    ) -> None:
        self.id = channel_id
        self.guild = guild
        self.mention = f"<#{channel_id}>"
        self._permissions = permissions or FakePermissions()
        self.sent: list[SentMessage] = []

    def permissions_for(self, _member: object) -> FakePermissions:
        return self._permissions

    async def send(self, content: str | None = None, **kwargs: object) -> SentMessage:
        message = SentMessage(content=content, extras=dict(kwargs))
        self.sent.append(message)
        return message


class FakeGuild:
    def __init__(
        self,
        *,
        guild_id: int,
        members: dict[int, FakeMember] | None = None,
        roles: dict[int, FakeRole] | None = None,
        channels: dict[int, FakeTextChannel] | None = None,
    ) -> None:
        self.id = guild_id
        self._members = members or {}
        self._roles = roles or {}
        self._channels = channels or {}

    def get_member(self, user_id: int) -> FakeMember | None:
        return self._members.get(user_id)

    def get_role(self, role_id: int) -> FakeRole | None:
        return self._roles.get(role_id)

    def get_channel(self, channel_id: int) -> FakeTextChannel | None:
        return self._channels.get(channel_id)


class FakeResponse:
    """Stand-in for ``interaction.response`` that records defers and direct replies."""

    def __init__(self) -> None:
        self.deferred = False
        self.deferred_ephemeral = False
        self.messages: list[SentMessage] = []

    async def defer(self, *, ephemeral: bool = False, thinking: bool = False) -> None:
        self.deferred = True
        self.deferred_ephemeral = ephemeral

    async def send_message(
        self,
        content: str | None = None,
        *,
        ephemeral: bool = False,
        **kwargs: object,
    ) -> None:
        self.messages.append(
            SentMessage(content=content, ephemeral=ephemeral, extras=dict(kwargs))
        )


class FakeFollowup:
    """Stand-in for ``interaction.followup`` that records messages."""

    def __init__(self) -> None:
        self.messages: list[SentMessage] = []

    async def send(
        self,
        content: str | None = None,
        *,
        ephemeral: bool = False,
        **kwargs: object,
    ) -> None:
        self.messages.append(
            SentMessage(content=content, ephemeral=ephemeral, extras=dict(kwargs))
        )


class FakeInteraction:
    def __init__(
        self,
        *,
        guild: FakeGuild | None = None,
        user: FakeMember | None = None,
        channel: FakeTextChannel | None = None,
    ) -> None:
        self.guild = guild
        self.user = user
        self.channel = channel
        self.response = FakeResponse()
        self.followup = FakeFollowup()

    @property
    def all_messages(self) -> list[SentMessage]:
        """Every reply sent through either the response or the followup surface."""
        return [*self.response.messages, *self.followup.messages]
