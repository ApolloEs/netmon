"""Shared utilities used across all netmon modules."""

from __future__ import annotations

import logging
import logging.handlers
import re
from datetime import datetime, timezone
from pathlib import Path

# Repo root — anchor for relative log paths so the file lands here even
# when the process runs as a service with a system CWD (e.g. System32).
_REPO_ROOT = Path(__file__).parent.parent

# Dotted-quad matcher, shared by the pinger (gateway parsing) and queries
# (splitting IP anchors from hostname targets when grouping outages).
IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def now() -> datetime:
    return datetime.now(timezone.utc)


def setup_logging(level: str, log_file: str, max_bytes: int, backup_count: int) -> None:
    """
    Configure root logger with a rotating file handler and a stderr handler.
    Call once at process startup before any other module is imported.
    Relative log paths are resolved against the repo root, not the CWD.
    """
    log_path = Path(log_file)
    if not log_path.is_absolute():
        log_path = _REPO_ROOT / log_path
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Rotating file — keeps the last N × max_bytes of logs
    fh = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # stderr for interactive runs
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)
