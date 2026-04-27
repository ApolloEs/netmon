"""
Outage reconciler — safety net for open outage records.

The pinger opens and closes outage records in real time. This module
handles the case where the process was killed while an outage was open,
leaving a record with no ended_at. Call reconcile() at startup and
periodically as a background safety net.

Public API:
    reconcile(engine, conf) — close any outage records that should be closed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.engine import Engine

from netmon import config as cfg
from netmon.db import connectivity_pings, outages

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _close(engine: Engine, outage_id: int, ended_at: datetime, started_at: datetime) -> None:
    duration = int((ended_at - started_at).total_seconds())
    with engine.begin() as conn:
        conn.execute(
            update(outages)
            .where(outages.c.id == outage_id)
            .values(ended_at=ended_at, duration_seconds=duration)
        )
    log.info("Reconciler closed outage #%d — duration %ds", outage_id, duration)


def reconcile(engine: Engine, conf: cfg.Config) -> None:
    """Close any outage records that should be closed."""
    threshold = conf.connectivity.outage_threshold_failures

    with engine.connect() as conn:
        open_outages = conn.execute(
            select(outages).where(outages.c.ended_at.is_(None))
        ).fetchall()

    if not open_outages:
        return

    log.info("Reconciler found %d open outage(s).", len(open_outages))

    for row in open_outages:
        with engine.connect() as conn:
            # Fetch recent pings for this target, newest first.
            recent = conn.execute(
                select(connectivity_pings)
                .where(connectivity_pings.c.target == row.trigger)
                .order_by(connectivity_pings.c.timestamp.desc())
                .limit(threshold)
            ).fetchall()

        if not recent:
            # No pings at all for this target — close at started_at as a
            # best-effort record with zero duration.
            log.warning(
                "Outage #%d has no ping records for target '%s'; closing at start time.",
                row.id, row.trigger,
            )
            _close(engine, row.id, row.started_at, row.started_at)
            continue

        all_success = all(p.success for p in recent)

        if all_success:
            # Connection recovered — close at the earliest recent successful
            # ping, but never before the outage started (guards against stale
            # ping rows predating this outage record).
            recovered_at = max(min(p.timestamp for p in recent), row.started_at)
            _close(engine, row.id, recovered_at, row.started_at)
        else:
            # Still failing or mixed — close at the last ping timestamp
            # so the record isn't left hanging indefinitely (e.g. pinger
            # was stopped while the outage was ongoing).
            last_ping_at = max(p.timestamp for p in recent)
            if last_ping_at > row.started_at:
                log.warning(
                    "Outage #%d appears ongoing or pinger stopped; "
                    "closing at last known ping (%s).",
                    row.id, last_ping_at.isoformat(),
                )
                _close(engine, row.id, last_ping_at, row.started_at)
