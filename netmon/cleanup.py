"""Data retention jobs — keeps tables from growing unbounded."""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import delete
from sqlalchemy.engine import Engine

from netmon.db import connectivity_pings
from netmon.utils import now

log = logging.getLogger(__name__)


def prune_pings(engine: Engine, retention_days: int = 7) -> None:
    """Delete connectivity_pings rows older than retention_days."""
    cutoff = now() - timedelta(days=retention_days)
    with engine.begin() as conn:
        result = conn.execute(
            delete(connectivity_pings).where(connectivity_pings.c.timestamp < cutoff)
        )
    deleted = result.rowcount
    if deleted:
        log.info("Pruned %d ping rows older than %d days.", deleted, retention_days)
