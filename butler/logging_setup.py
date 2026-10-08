"""Process logging setup: coloured levels + UTC ISO timestamps."""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime
from typing import ClassVar

import discord.utils


class UtcColourFormatter(logging.Formatter):
    """discord.py-style colours with UTC ISO-8601 timestamps (ms + Z)."""

    LEVEL_COLOURS: ClassVar[list[tuple[int, str]]] = [
        (logging.DEBUG, "\x1b[40;1m"),
        (logging.INFO, "\x1b[34;1m"),
        (logging.WARNING, "\x1b[33;1m"),
        (logging.ERROR, "\x1b[31m"),
        (logging.CRITICAL, "\x1b[41m"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._formats = {
            level: logging.Formatter(
                fmt=(
                    f"\x1b[30;1m%(asctime)s\x1b[0m {colour}%(levelname)-8s\x1b[0m "
                    f"\x1b[35m%(name)s\x1b[0m %(message)s"
                ),
                datefmt=None,
            )
            for level, colour in self.LEVEL_COLOURS
        }
        for formatter in self._formats.values():
            formatter.formatTime = self.formatTime  # type: ignore[method-assign]

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        _ = datefmt
        when = datetime.fromtimestamp(record.created, tz=UTC)
        return when.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(when.microsecond / 1000):03d}Z"

    def format(self, record: logging.LogRecord) -> str:
        formatter = self._formats.get(record.levelno) or self._formats[logging.DEBUG]
        if record.exc_info:
            text = formatter.formatException(record.exc_info)
            record.exc_text = f"\x1b[31m{text}\x1b[0m"
        try:
            return formatter.format(record)
        finally:
            record.exc_text = None


def configure_logging(*, level: int = logging.INFO) -> None:
    """Configure root logging with colour + UTC timestamps.

    Call once before ``bot.run``. Pass ``log_handler=None`` to ``bot.run`` so
    discord.py does not install a second default handler.
    """
    use_colour = False
    stream = sys.stderr
    try:
        use_colour = bool(discord.utils.stream_supports_colour(stream))
    except Exception:
        use_colour = False

    if use_colour:
        formatter: logging.Formatter = UtcColourFormatter()
    else:
        plain = logging.Formatter(
            fmt="%(asctime)s %(levelname)-8s %(name)s %(message)s",
        )
        plain.formatTime = UtcColourFormatter().formatTime  # type: ignore[method-assign]
        formatter = plain

    root = logging.getLogger()
    root.setLevel(level)
    for existing in list(root.handlers):
        root.removeHandler(existing)

    handler = logging.StreamHandler(stream=stream)
    handler.setLevel(level)
    handler.setFormatter(formatter)
    root.addHandler(handler)

    for name in ("discord", "discord.http", "discord.gateway", "asyncio"):
        lib_logger = logging.getLogger(name)
        lib_logger.handlers.clear()
        lib_logger.setLevel(level)
        lib_logger.propagate = True

    logging.getLogger("apscheduler").setLevel(logging.WARNING)
