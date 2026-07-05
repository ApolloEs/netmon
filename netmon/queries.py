"""
Dashboard DB queries. All aggregation happens in SQL — never pull raw rows
to Python and aggregate there.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine


def _row_to_dict(row) -> dict:
    return dict(row._mapping)


def get_status(engine: Engine) -> dict[str, Any]:
    """
    Returns the current connection status for the status strip.
    Includes last ping per target and the most recent speed test.
    """
    with engine.connect() as conn:
        ping_rows = conn.execute(text("""
            SELECT DISTINCT ON (target)
                target, success, latency_ms, timestamp
            FROM connectivity_pings
            ORDER BY target, timestamp DESC
        """)).fetchall()

        speed_row = conn.execute(text("""
            SELECT download_mbps, upload_mbps, ping_ms,
                   pct_of_target, target_mbps, timestamp
            FROM speed_tests
            ORDER BY timestamp DESC
            LIMIT 1
        """)).fetchone()

        open_outage = conn.execute(text("""
            SELECT id, started_at, trigger
            FROM outages
            WHERE ended_at IS NULL
            ORDER BY started_at DESC
            LIMIT 1
        """)).fetchone()

    targets = [_row_to_dict(r) for r in ping_rows]
    for t in targets:
        t["timestamp"] = t["timestamp"].isoformat() if t["timestamp"] else None

    if targets:
        successes = [t["success"] for t in targets]
        if all(successes):
            status = "online"
        elif not any(successes):
            status = "offline"
        else:
            status = "degraded"
    else:
        status = "unknown"

    last_speed = _row_to_dict(speed_row) if speed_row else None
    if last_speed:
        last_speed["timestamp"] = last_speed["timestamp"].isoformat()

    open_outage_dict = _row_to_dict(open_outage) if open_outage else None
    if open_outage_dict:
        open_outage_dict["started_at"] = open_outage_dict["started_at"].isoformat()

    return {
        "status": status,
        "targets": targets,
        "last_speed": last_speed,
        "open_outage": open_outage_dict,
    }


def get_speed_history(engine: Engine, days: int = 30) -> list[dict]:
    """Speed test results for the speed-over-time chart."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    timestamp,
                    download_mbps, upload_mbps, ping_ms,
                    pct_of_target, target_mbps
                FROM speed_tests
                WHERE timestamp >= NOW() - (:days * INTERVAL '1 day')
                ORDER BY timestamp
            """),
            {"days": days},
        ).fetchall()
    result = [_row_to_dict(r) for r in rows]
    for r in result:
        r["timestamp"] = r["timestamp"].isoformat()
    return result


def get_test_events(engine: Engine, days: int = 30) -> list[dict]:
    """Test lifecycle events for speed chart annotations (postponed/skipped/forced)."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    timestamp,
                    status, reason
                FROM test_events
                WHERE timestamp >= NOW() - (:days * INTERVAL '1 day')
                  AND status IN ('postponed', 'skipped', 'forced', 'error')
                ORDER BY timestamp
            """),
            {"days": days},
        ).fetchall()
    result = [_row_to_dict(r) for r in rows]
    for r in result:
        r["timestamp"] = r["timestamp"].isoformat()
    return result


def get_outages(engine: Engine, days: int = 30) -> list[dict]:
    """Outage records for the timeline view."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    id,
                    started_at,
                    COALESCE(ended_at, NOW()) AS ended_at,
                    duration_seconds,
                    trigger,
                    ended_at IS NULL AS is_open
                FROM outages
                WHERE started_at >= NOW() - (:days * INTERVAL '1 day')
                ORDER BY started_at DESC
            """),
            {"days": days},
        ).fetchall()
    result = [_row_to_dict(r) for r in rows]
    for r in result:
        r["started_at"] = r["started_at"].isoformat()
        r["ended_at"] = r["ended_at"].isoformat()
    return result


def get_ping_heatmap(engine: Engine, days: int = 7) -> dict:
    """
    Packet loss % grouped by UTC hour and target.
    Returns targets list and a dict: {target: {hour: loss_pct}}.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    EXTRACT(HOUR FROM timestamp AT TIME ZONE 'UTC')::int AS hour,
                    target,
                    COUNT(*) AS total,
                    SUM(CASE WHEN NOT success THEN 1 ELSE 0 END) AS failures,
                    ROUND(
                        100.0 * SUM(CASE WHEN NOT success THEN 1 ELSE 0 END)
                        / NULLIF(COUNT(*), 0),
                    1) AS loss_pct
                FROM connectivity_pings
                WHERE timestamp >= NOW() - (:days * INTERVAL '1 day')
                GROUP BY hour, target
                ORDER BY target, hour
            """),
            {"days": days},
        ).fetchall()

    targets = sorted({r.target for r in rows})
    data: dict[str, dict[int, float]] = {t: {} for t in targets}
    for r in rows:
        data[r.target][r.hour] = float(r.loss_pct) if r.loss_pct is not None else 0.0

    return {"targets": targets, "by_target": data}


def get_adherence(engine: Engine) -> dict:
    """
    % of speed tests where download >= 80% of target, for 7d and 30d windows.
    """
    with engine.connect() as conn:
        def _query(days: int) -> dict:
            row = conn.execute(
                text("""
                    SELECT
                        COUNT(*) AS total,
                        COUNT(*) FILTER (WHERE pct_of_target >= 80) AS good,
                        ROUND(
                            100.0 * COUNT(*) FILTER (WHERE pct_of_target >= 80)
                            / NULLIF(COUNT(*), 0),
                        1) AS adherence_pct,
                        ROUND(AVG(download_mbps)::numeric, 1) AS avg_download,
                        ROUND(MIN(download_mbps)::numeric, 1) AS min_download,
                        MAX(target_mbps) AS target_mbps
                    FROM speed_tests
                    WHERE timestamp >= NOW() - (:days * INTERVAL '1 day')
                """),
                {"days": days},
            ).fetchone()
            return _row_to_dict(row) if row else {}

        return {"7d": _query(7), "30d": _query(30)}
