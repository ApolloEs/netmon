"""
Dashboard DB queries. All aggregation happens in SQL — never pull raw rows
to Python and aggregate there.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from netmon.pinger import _IPV4_RE


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


# Anchor outages (IP targets) within this gap of each other merge into one
# connection event; targets rarely fail at the exact same ping cycle.
_MERGE_GAP = timedelta(seconds=60)
# A host (hostname target) outage folds into a connection event when its
# interval sits within the event, allowing this much detection jitter
# (~one detection window: threshold × ping interval).
_FOLD_TOLERANCE = timedelta(seconds=120)


def get_outages(engine: Engine, days: int = 30) -> list[dict]:
    """
    Outage events for the timeline view, grouped for display:

    - IP-anchor outages (gateway, 1.1.1.1, …) that overlap are merged into
      a single "connection" event — one WAN drop, one row.
    - Hostname outages (google.com, …) contained in a connection event fold
      into it; otherwise they stand alone as "host" events (site/DNS down
      while the connection itself was fine).

    DB rows stay per-target; grouping is read-time only. Interval-merging
    is done in Python — the one exception to the aggregate-in-SQL rule.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    started_at,
                    COALESCE(ended_at, NOW()) AS ended_at,
                    trigger,
                    ended_at IS NULL AS is_open
                FROM outages
                WHERE started_at >= NOW() - (:days * INTERVAL '1 day')
                ORDER BY started_at
            """),
            {"days": days},
        ).fetchall()
    return _group_outages(rows)


def _group_outages(rows) -> list[dict]:
    """Pure grouping step; rows need started_at, ended_at, trigger, is_open."""
    anchors = [r for r in rows if _IPV4_RE.match(r.trigger or "")]
    hosts = [r for r in rows if not _IPV4_RE.match(r.trigger or "")]

    # 1. Merge overlapping/adjacent anchor outages into connection events.
    clusters: list[dict] = []
    for r in anchors:  # already sorted by started_at
        if clusters and r.started_at <= clusters[-1]["end"] + _MERGE_GAP:
            c = clusters[-1]
            c["end"] = max(c["end"], r.ended_at)
            c["triggers"].add(r.trigger)
            c["is_open"] = c["is_open"] or r.is_open
        else:
            clusters.append({
                "type": "connection",
                "start": r.started_at,
                "end": r.ended_at,
                "triggers": {r.trigger},
                "is_open": r.is_open,
            })

    # 2. Fold contained host outages into their connection event; the rest
    #    stand alone.
    for r in hosts:
        folded = False
        for c in clusters:
            if (r.started_at >= c["start"] - _FOLD_TOLERANCE
                    and r.ended_at <= c["end"] + _FOLD_TOLERANCE):
                c["start"] = min(c["start"], r.started_at)
                c["end"] = max(c["end"], r.ended_at)
                c["triggers"].add(r.trigger)
                c["is_open"] = c["is_open"] or r.is_open
                folded = True
                break
        if not folded:
            clusters.append({
                "type": "host",
                "start": r.started_at,
                "end": r.ended_at,
                "triggers": {r.trigger},
                "is_open": r.is_open,
            })

    clusters.sort(key=lambda c: c["start"], reverse=True)
    return [
        {
            "type": c["type"],
            "started_at": c["start"].isoformat(),
            "ended_at": c["end"].isoformat(),
            "duration_seconds": (
                None if c["is_open"]
                else int((c["end"] - c["start"]).total_seconds())
            ),
            "triggers": sorted(c["triggers"]),
            "is_open": c["is_open"],
        }
        for c in clusters
    ]


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


def get_data_cost(engine: Engine, days: int = 30) -> dict:
    """
    Average data cost per speed test (MB), preferring real byte counts.
    Falls back to an estimate from recorded speeds (~15s per direction)
    for databases with no byte data yet; flags which one it is.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT
                    AVG(download_bytes + upload_bytes) / 1e6 AS avg_mb,
                    COUNT(*) AS n
                FROM speed_tests
                WHERE timestamp >= NOW() - (:days * INTERVAL '1 day')
                  AND download_bytes IS NOT NULL
                  AND upload_bytes IS NOT NULL
            """),
            {"days": days},
        ).fetchone()

        if row and row.n > 0:
            return {
                "avg_mb_per_test": round(float(row.avg_mb), 1),
                "sample_count": row.n,
                "estimated": False,
            }

        est = conn.execute(
            text("""
                SELECT
                    (AVG(download_mbps) + AVG(upload_mbps)) / 8 * 15 AS avg_mb,
                    COUNT(*) AS n
                FROM speed_tests
                WHERE timestamp >= NOW() - (:days * INTERVAL '1 day')
            """),
            {"days": days},
        ).fetchone()

    if est and est.n > 0 and est.avg_mb is not None:
        return {
            "avg_mb_per_test": round(float(est.avg_mb), 1),
            "sample_count": est.n,
            "estimated": True,
        }
    # No tests at all — fall back to the ballpark from CLAUDE.md.
    return {"avg_mb_per_test": 400.0, "sample_count": 0, "estimated": True}


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
