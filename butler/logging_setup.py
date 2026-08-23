"""Optional logging helpers (prefer bot.run(root_logger=True) at startup)."""

from __future__ import annotations

import logging

import discord


def configure_logging(*, level: int = logging.INFO) -> None:
    """Configure root logging with discord.py's colour-aware formatter.

    Prefer ``bot.run(..., root_logger=True)`` so setup happens once inside Client.run.
    Use this only when not starting via Client.run.
    """
    discord.utils.setup_logging(level=level, root=True)
