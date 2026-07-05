"""
Outage reconciler — safety net for open outage records.

The pinger opens and closes outage records in real time. This module
handles the case where the process was killed while an outage was open,
leaving a record with no ended_at. Call reconcile() at startup and
periodically as a background safety net.

An open outage is closed only when one of these holds:
  - recovery is confirmed (threshold consecutive successful pings), or
  - the target has no pings at all (data anomaly), or
  - the newest ping for the target is stale — the pinger stopped
    reporting — in which case we close at the last known ping.

An outage that is still failing with fresh pings is genuinely ongoing
and must stay open; closing it would destroy the duration evidence.

Public API:
    reconcile(engine, conf) — close any outage records that should be closed.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.engine import Engine

from netmon import config as cfg
from netmon.db import connectivity_pings, outages
from netmon.utils import now

log = logging.getLogger(__name__)

# The pinger is considered dead for a target when its newest ping is older
# than this many ping intervals.
STALE_INTERVALS = 10


def _close(engine: Engine, outage_id: int, ended_at, started_at) -> None:
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
    stale_after = timedelta(
        seconds=STALE_INTERVALS * conf.connectivity.ping_interval_seconds
    )

    with engine.connect() as conn:
        open_outages = conn.execute(
            select(outages).where(outages.c.ended_at.is_(None))
        ).fetchall()

    if not open_outages:
        return

    log.info("Reconciler found %d open outage(s).", len(open_outages))

    for row in open_outages:
        with engine.connect() as conn:
            recent = conn.execute(
                select(connectivity_pings)
                .where(connectivity_pings.c.target == row.trigger)
                .order_by(connectivity_pings.c.timestamp.desc())
                .limit(threshold)
            ).fetchall()

        if not recent:
            log.warning(
                "Outage #%d has no ping records for target '%s'; closing at start time.",
                row.id, row.trigger,
            )
            _close(engine, row.id, row.started_at, row.started_at)
            continue

        # Require a full streak of `threshold` successes to confirm recovery.
        # If we have fewer than threshold pings, we can't confirm yet.
        confirmed_recovery = (
            len(recent) >= threshold and all(p.success for p in recent)
        )

        if confirmed_recovery:
            # Close at the earliest of the recent successful pings, but never
            # before the outage started (guards against stale pre-outage rows).
            recovered_at = max(min(p.timestamp for p in recent), row.started_at)
            _close(engine, row.id, recovered_at, row.started_at)
            continue

        last_ping_at = max(p.timestamp for p in recent)
        if now() - last_ping_at > stale_after:
            # The pinger stopped reporting on this target — nothing more will
            # confirm recovery, so close at the last known ping.
            close_at = max(last_ping_at, row.started_at)
            log.warning(
                "Outage #%d: no pings for '%s' since %s (pinger stopped); "
                "closing at last known ping.",
                row.id, row.trigger, last_ping_at.isoformat(),
            )
            _close(engine, row.id, close_at, row.started_at)
        else:
            # Pings are fresh and still failing (or mixed): the outage is
            # genuinely ongoing. Leave it open — the pinger will close it.
            log.info(
                "Outage #%d still ongoing for '%s' (last ping %s); leaving open.",
                row.id, row.trigger, last_ping_at.isoformat(),
            )
