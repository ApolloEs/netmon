"""Data retention jobs — keeps tables from growing unbounded."""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import delete
from sqlalchemy.engine import Engine

from netmon.db import connectivity_pings, host_throughput
from netmon.utils import now

log = logging.getLogger(__name__)


def _prune_table(engine: Engine, table, label: str, retention_days: int) -> None:
    cutoff = now() - timedelta(days=retention_days)
    with engine.begin() as conn:
        result = conn.execute(delete(table).where(table.c.timestamp < cutoff))
    if result.rowcount:
        log.info("Pruned %d %s rows older than %d days.", result.rowcount, label, retention_days)


def prune_pings(engine: Engine, retention_days: int = 7) -> None:
    """Delete raw high-volume rows (pings, throughput samples) past retention."""
    _prune_table(engine, connectivity_pings, "ping", retention_days)
    _prune_table(engine, host_throughput, "throughput", retention_days)
