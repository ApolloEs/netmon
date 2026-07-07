"""
Degraded-period detector — sustained packet loss below outage level.

An outage means a target died for consecutive pings; a degraded period
means the line stayed up but dropped >= threshold % of packets across
internet targets over a sustained window. Both matter as ISP evidence,
and raw pings are pruned after 7 days, so degraded periods are persisted
in their own table.

Windows are aligned to the epoch grid (floor(ts / window)) so processing
is deterministic and restart-safe: an open period's exact coverage is
started_at + windows_count * window, which is where evaluation resumes.
On a fresh table the full ping retention is backfilled automatically.

Public API:
    evaluate(engine, conf) — process all complete windows since the last
                             processed one; returns True if records changed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import insert, select, text, update
from sqlalchemy.engine import Engine

from netmon import config as cfg
from netmon.db import PRIVATE_IP_SQL, connectivity_pings, degraded_periods
from netmon.utils import now

log = logging.getLogger(__name__)

# Below this many pings in a window, the monitor was (mostly) off — the
# window is unknowable, not clean, so it closes any open period.
MIN_SAMPLES = 5


def _floor_to_window(ts: datetime, window_s: int) -> datetime:
    epoch = int(ts.timestamp())
    return datetime.fromtimestamp(epoch - epoch % window_s, tz=timezone.utc)


def _window_losses(engine: Engine, start: datetime, end: datetime, window_s: int) -> dict:
    """{window_start_ts: (total, fails)} for internet-target pings."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    FLOOR(EXTRACT(EPOCH FROM timestamp) / :wsec) * :wsec AS w,
                    COUNT(*) AS total,
                    SUM(CASE WHEN NOT success THEN 1 ELSE 0 END) AS fails
                FROM connectivity_pings
                WHERE timestamp >= :start AND timestamp < :end
                  AND target !~ :priv
                GROUP BY w
            """),
            {"wsec": window_s, "start": start, "end": end, "priv": PRIVATE_IP_SQL},
        ).fetchall()
    return {int(r.w): (r.total, r.fails) for r in rows}


def _get_open(conn):
    return conn.execute(
        select(degraded_periods)
        .where(degraded_periods.c.ended_at.is_(None))
        .order_by(degraded_periods.c.started_at.desc())
        .limit(1)
    ).fetchone()


def _coverage_end(row, window_s: int) -> datetime:
    return row.started_at + timedelta(seconds=row.windows_count * window_s)


def _resume_point(engine: Engine, window_s: int) -> datetime | None:
    """Where evaluation left off; None means empty table (backfill)."""
    with engine.connect() as conn:
        open_row = _get_open(conn)
        if open_row is not None:
            return _coverage_end(open_row, window_s)
        last_end = conn.execute(
            select(degraded_periods.c.ended_at)
            .order_by(degraded_periods.c.ended_at.desc())
            .limit(1)
        ).scalar()
    return last_end


def _oldest_ping(engine: Engine) -> datetime | None:
    with engine.connect() as conn:
        return conn.execute(
            select(connectivity_pings.c.timestamp)
            .order_by(connectivity_pings.c.timestamp)
            .limit(1)
        ).scalar()


def evaluate(engine: Engine, conf: cfg.Config) -> bool:
    window_s = conf.connectivity.degraded_window_minutes * 60
    threshold = conf.connectivity.degraded_loss_threshold_pct

    horizon_end = _floor_to_window(now(), window_s)

    resume = _resume_point(engine, window_s)
    if resume is None:
        oldest = _oldest_ping(engine)
        if oldest is None:
            return False
        resume = _floor_to_window(oldest, window_s)
        log.info(
            "Degraded detector: empty table — backfilling from %s (%d windows).",
            resume.isoformat(), int((horizon_end - resume).total_seconds()) // window_s,
        )
    # Never reach past ping retention.
    retention_floor = _floor_to_window(now() - timedelta(days=7), window_s)
    resume = max(resume, retention_floor)

    if resume >= horizon_end:
        return False

    losses = _window_losses(engine, resume, horizon_end, window_s)
    changed = False

    with engine.begin() as conn:
        open_row = _get_open(conn)
        w = resume
        while w < horizon_end:
            w_epoch = int(w.timestamp())
            total, fails = losses.get(w_epoch, (0, 0))

            if total < MIN_SAMPLES:
                # Monitor off / unknowable window: close conservatively.
                if open_row is not None:
                    changed |= _close(conn, open_row, _coverage_end(open_row, window_s))
                    open_row = None
                w += timedelta(seconds=window_s)
                continue

            loss = 100.0 * fails / total

            if loss >= threshold:
                if open_row is None:
                    result = conn.execute(
                        insert(degraded_periods)
                        .values(
                            started_at=w,
                            avg_loss_pct=round(loss, 1),
                            peak_loss_pct=round(loss, 1),
                            windows_count=1,
                        )
                        .returning(degraded_periods.c.id)
                    )
                    log.warning(
                        "Degraded period #%d opened at %s — %.1f%% loss.",
                        result.scalar_one(), w.isoformat(), loss,
                    )
                    open_row = _get_open(conn)
                else:
                    n = open_row.windows_count
                    new_avg = (open_row.avg_loss_pct * n + loss) / (n + 1)
                    conn.execute(
                        update(degraded_periods)
                        .where(degraded_periods.c.id == open_row.id)
                        .values(
                            avg_loss_pct=round(new_avg, 1),
                            peak_loss_pct=round(max(open_row.peak_loss_pct, loss), 1),
                            windows_count=n + 1,
                        )
                    )
                    open_row = _get_open(conn)
                changed = True
            else:
                if open_row is not None:
                    changed |= _close(conn, open_row, w)
                    open_row = None

            w += timedelta(seconds=window_s)

    return changed


def _close(conn, row, ended_at) -> bool:
    duration = int((ended_at - row.started_at).total_seconds())
    if duration <= 0:
        # Degenerate (shouldn't happen with aligned windows) — close flat.
        ended_at = row.started_at
        duration = 0
    conn.execute(
        update(degraded_periods)
        .where(degraded_periods.c.id == row.id)
        .values(ended_at=ended_at, duration_seconds=duration)
    )
    log.info(
        "Degraded period #%d closed — %ds, avg %.1f%% / peak %.1f%% loss.",
        row.id, duration, row.avg_loss_pct, row.peak_loss_pct,
    )
    return True
