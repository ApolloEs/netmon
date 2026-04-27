"""Shared utilities used across all netmon modules."""

from __future__ import annotations

import logging
import logging.handlers
from datetime import datetime, timezone


def now() -> datetime:
    return datetime.now(timezone.utc)


def setup_logging(level: str, log_file: str, max_bytes: int, backup_count: int) -> None:
    """
    Configure root logger with a rotating file handler and a stderr handler.
    Call once at process startup before any other module is imported.
    """
    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Rotating file — keeps the last N × max_bytes of logs
    fh = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # stderr for interactive runs
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)
