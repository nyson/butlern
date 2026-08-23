"""Reusable discord.py interval job factory.

Pattern used by daily event-cache resync and available for other long-running jobs:
- ``tasks.loop(hours=...)`` (or minutes/seconds)
- ``before_loop`` waits until the bot is ready
- optional skip of the first iteration when boot already did the work
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from discord.ext import commands, tasks

logger = logging.getLogger(__name__)

JobCoro = Callable[[], Awaitable[None]]
BotFactory = Callable[[], commands.Bot]


@dataclass(frozen=True)
class IntervalJob:
    """Handle for a started (or startable) interval task loop."""

    loop: Any
    name: str

    def start(self) -> None:
        if not self.loop.is_running():
            self.loop.start()
            logger.info("Started interval job %s.", self.name)

    def stop(self) -> None:
        if self.loop.is_running():
            self.loop.stop()
            logger.info("Stopped interval job %s.", self.name)

    def cancel(self) -> None:
        """Cancel the underlying task loop (test/teardown helper)."""
        self.loop.cancel()

    def is_running(self) -> bool:
        return bool(self.loop.is_running())

    @property
    def current_loop(self) -> int:
        return int(self.loop.current_loop)


def create_interval_job(
    *,
    name: str,
    get_bot: BotFactory,
    run: JobCoro,
    hours: float | None = None,
    minutes: float | None = None,
    seconds: float | None = None,
    skip_first_iteration: bool = False,
    first_iteration_log: str | None = None,
) -> IntervalJob:
    """Build a discord.ext.tasks loop with standard ready-wait wiring.

    Parameters
    ----------
    name:
        Stable job name for logs.
    get_bot:
        Callable returning the live bot (often ``lambda: bot``).
    run:
        Async body executed each interval (after optional first-iteration skip).
    hours / minutes / seconds:
        Same kwargs as ``tasks.loop``. At least one must be set.
    skip_first_iteration:
        When True, iteration 0 returns immediately. Useful when boot already
        performed the same work and ``loop.start()`` would otherwise double-run.
    first_iteration_log:
        Optional log line when skipping the first iteration.
    """
    if hours is None and minutes is None and seconds is None:
        raise ValueError("create_interval_job requires hours, minutes, and/or seconds")

    # Construct with explicit kwargs so type checkers accept discord.ext.tasks.loop.
    if hours is not None and minutes is None and seconds is None:
        loop_decorator = tasks.loop(hours=hours)
    elif minutes is not None and hours is None and seconds is None:
        loop_decorator = tasks.loop(minutes=minutes)
    elif seconds is not None and hours is None and minutes is None:
        loop_decorator = tasks.loop(seconds=seconds)
    else:
        # Mixed units: fall back to seconds total.
        total_seconds = (hours or 0) * 3600 + (minutes or 0) * 60 + (seconds or 0)
        if total_seconds <= 0:
            raise ValueError("interval duration must be positive")
        loop_decorator = tasks.loop(seconds=total_seconds)

    @loop_decorator
    async def _loop() -> None:
        if skip_first_iteration and _loop.current_loop == 0:
            msg = first_iteration_log or (
                f"Skipping immediate run of job {name} (boot path already covered it)."
            )
            logger.info(msg)
            return
        await run()

    @_loop.before_loop
    async def _wait_until_ready() -> None:
        await get_bot().wait_until_ready()

    return IntervalJob(loop=_loop, name=name)
