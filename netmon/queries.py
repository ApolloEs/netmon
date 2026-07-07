"""
Dashboard DB queries. All aggregation happens in SQL — never pull raw rows
to Python and aggregate there.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from netmon.db import PRIVATE_IP_SQL as _PRIVATE_IP_SQL
from netmon.utils import IPV4_RE


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
    anchors = [r for r in rows if IPV4_RE.match(r.trigger or "")]
    hosts = [r for r in rows if not IPV4_RE.match(r.trigger or "")]

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


def get_degraded(engine: Engine, days: int = 30) -> list[dict]:
    """Degraded periods (sustained packet loss), newest-first."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT started_at,
                       COALESCE(ended_at, NOW()) AS ended_at,
                       duration_seconds, avg_loss_pct, peak_loss_pct,
                       ended_at IS NULL AS is_open
                FROM degraded_periods
                WHERE started_at >= NOW() - (:days * INTERVAL '1 day')
                ORDER BY started_at DESC
            """),
            {"days": days},
        ).fetchall()
    return [
        {
            "type": "degraded",
            "started_at": r.started_at.isoformat(),
            "ended_at": r.ended_at.isoformat(),
            "duration_seconds": None if r.is_open else r.duration_seconds,
            "avg_loss_pct": r.avg_loss_pct,
            "peak_loss_pct": r.peak_loss_pct,
            "is_open": r.is_open,
        }
        for r in rows
    ]


def get_ping_heatmap(engine: Engine, days: int = 7) -> dict:
    """
    Packet loss % grouped by *local* hour-of-day and target — the goal is
    spotting "it's always bad at 8pm" in the user's clock, not UTC.
    Returns targets list and a dict: {target: {hour: loss_pct}}.
    """
    # Host's current UTC offset; DST drift within a 7-day window is
    # negligible for an hour-of-day rollup.
    offset_hours = int(
        datetime.now().astimezone().utcoffset().total_seconds() // 3600
    )
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    EXTRACT(HOUR FROM (timestamp AT TIME ZONE 'UTC')
                            + make_interval(hours => :off))::int AS hour,
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
            {"days": days, "off": offset_hours},
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


def get_latency_history(engine: Engine, days: int = 7) -> list[dict]:
    """
    Average and p95 latency of successful pings to *internet* targets,
    bucketed to 5 minutes. Ping retention is 7 days, so days is capped.
    """
    days = min(days, 7)
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    to_timestamp(FLOOR(EXTRACT(EPOCH FROM timestamp) / 300) * 300) AS bucket,
                    AVG(latency_ms) AS avg_ms,
                    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms
                FROM connectivity_pings
                WHERE timestamp >= NOW() - (:days * INTERVAL '1 day')
                  AND success AND latency_ms IS NOT NULL
                  AND target !~ :priv
                GROUP BY bucket
                ORDER BY bucket
            """),
            {"days": days, "priv": _PRIVATE_IP_SQL},
        ).fetchall()
    return [
        {
            "t": r.bucket.isoformat(),
            "avg_ms": round(float(r.avg_ms), 1),
            "p95_ms": round(float(r.p95_ms), 1),
        }
        for r in rows
    ]


def get_daily_summary(engine: Engine, days: int = 30) -> list[dict]:
    """
    Per local-calendar-day rollup for the quality calendar: tests, average
    download, adherence %, and connection-outage seconds (attributed to
    the day the outage started).
    """
    offset_hours = int(
        datetime.now().astimezone().utcoffset().total_seconds() // 3600
    )
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    ((timestamp AT TIME ZONE 'UTC')
                     + make_interval(hours => :off))::date AS day,
                    COUNT(*) AS tests,
                    AVG(download_mbps) AS dl_mean,
                    COUNT(*) FILTER (WHERE pct_of_target >= 80) AS good
                FROM speed_tests
                WHERE timestamp >= NOW() - (:days * INTERVAL '1 day')
                GROUP BY day
                ORDER BY day
            """),
            {"days": days, "off": offset_hours},
        ).fetchall()

    by_day: dict[str, dict] = {}
    for r in rows:
        by_day[r.day.isoformat()] = {
            "day": r.day.isoformat(),
            "tests": r.tests,
            "dl_mean": round(float(r.dl_mean), 1) if r.dl_mean is not None else None,
            "adherence_pct": round(100.0 * r.good / r.tests, 1) if r.tests else None,
            "outage_seconds": 0,
        }

    tz = datetime.now().astimezone().tzinfo

    def _day_entry(day: str) -> dict:
        return by_day.setdefault(day, {
            "day": day, "tests": 0, "dl_mean": None,
            "adherence_pct": None, "outage_seconds": 0, "degraded_seconds": 0,
        })

    for entry in by_day.values():
        entry.setdefault("degraded_seconds", 0)

    for o in get_outages(engine, days=days):
        if o["type"] != "connection":
            continue
        day = datetime.fromisoformat(o["started_at"]).astimezone(tz).date().isoformat()
        _day_entry(day)["outage_seconds"] += o["duration_seconds"] or 0

    for p in get_degraded(engine, days=days):
        day = datetime.fromisoformat(p["started_at"]).astimezone(tz).date().isoformat()
        _day_entry(day)["degraded_seconds"] += p["duration_seconds"] or 0

    return sorted(by_day.values(), key=lambda d: d["day"])


def get_report_stats(engine: Engine, days: int = 30) -> dict:
    """
    Everything the ISP evidence report needs, in one dict. Peak/off-peak
    and hourly figures use the host's local clock (same offset technique
    as get_ping_heatmap).
    """
    offset_hours = int(
        datetime.now().astimezone().utcoffset().total_seconds() // 3600
    )
    params = {"days": days, "off": offset_hours}

    with engine.connect() as conn:
        speed = conn.execute(
            text("""
                SELECT
                    COUNT(*) AS n,
                    AVG(download_mbps) AS dl_mean,
                    PERCENTILE_CONT(0.5)  WITHIN GROUP (ORDER BY download_mbps) AS dl_median,
                    PERCENTILE_CONT(0.05) WITHIN GROUP (ORDER BY download_mbps) AS dl_p5,
                    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY download_mbps) AS dl_p95,
                    MIN(download_mbps) AS dl_min,
                    MAX(download_mbps) AS dl_max,
                    AVG(upload_mbps) AS ul_mean,
                    AVG(ping_ms) AS ping_mean,
                    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY ping_ms) AS ping_p95,
                    COUNT(*) FILTER (WHERE pct_of_target >= 80)  AS n_adh80,
                    COUNT(*) FILTER (WHERE pct_of_target >= 100) AS n_adh100
                FROM speed_tests
                WHERE timestamp >= NOW() - (:days * INTERVAL '1 day')
            """),
            params,
        ).one()

        bands = conn.execute(
            text("""
                SELECT
                    CASE
                        WHEN EXTRACT(HOUR FROM (timestamp AT TIME ZONE 'UTC')
                             + make_interval(hours => :off)) BETWEEN 18 AND 23 THEN 'peak'
                        WHEN EXTRACT(HOUR FROM (timestamp AT TIME ZONE 'UTC')
                             + make_interval(hours => :off)) BETWEEN 2 AND 5 THEN 'offpeak'
                        ELSE 'other'
                    END AS band,
                    COUNT(*) AS n,
                    AVG(download_mbps) AS dl_mean,
                    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY download_mbps) AS dl_median
                FROM speed_tests
                WHERE timestamp >= NOW() - (:days * INTERVAL '1 day')
                GROUP BY band
            """),
            params,
        ).fetchall()

        hourly = conn.execute(
            text("""
                SELECT
                    EXTRACT(HOUR FROM (timestamp AT TIME ZONE 'UTC')
                            + make_interval(hours => :off))::int AS hour,
                    AVG(download_mbps) AS dl_mean,
                    COUNT(*) AS n
                FROM speed_tests
                WHERE timestamp >= NOW() - (:days * INTERVAL '1 day')
                GROUP BY hour ORDER BY hour
            """),
            params,
        ).fetchall()

        history = conn.execute(
            text("""
                SELECT timestamp, download_mbps
                FROM speed_tests
                WHERE timestamp >= NOW() - (:days * INTERVAL '1 day')
                ORDER BY timestamp
            """),
            params,
        ).fetchall()

        loss = conn.execute(
            text("""
                SELECT target,
                       COUNT(*) AS total,
                       SUM(CASE WHEN NOT success THEN 1 ELSE 0 END) AS fails
                FROM connectivity_pings
                WHERE timestamp >= NOW() - (:days * INTERVAL '1 day')
                GROUP BY target ORDER BY target
            """),
            params,
        ).fetchall()

    def _f(v):
        return round(float(v), 1) if v is not None else None

    band_map = {r.band: r for r in bands}

    def _band(name):
        r = band_map.get(name)
        if not r:
            return {"n": 0, "dl_mean": None, "dl_median": None}
        return {"n": r.n, "dl_mean": _f(r.dl_mean), "dl_median": _f(r.dl_median)}

    grouped = get_outages(engine, days=days)
    conn_events = [o for o in grouped if o["type"] == "connection"]
    host_events = [o for o in grouped if o["type"] == "host"]
    durations = [o["duration_seconds"] or 0 for o in conn_events]

    deg = get_degraded(engine, days=days)
    deg_durations = [d["duration_seconds"] or 0 for d in deg]

    return {
        "period_days": days,
        "tests": {
            "count": speed.n,
            "dl_mean": _f(speed.dl_mean), "dl_median": _f(speed.dl_median),
            "dl_p5": _f(speed.dl_p5), "dl_p95": _f(speed.dl_p95),
            "dl_min": _f(speed.dl_min), "dl_max": _f(speed.dl_max),
            "ul_mean": _f(speed.ul_mean),
            "ping_mean": _f(speed.ping_mean), "ping_p95": _f(speed.ping_p95),
            "adherence_80_pct": _f(100.0 * speed.n_adh80 / speed.n) if speed.n else None,
            "adherence_100_pct": _f(100.0 * speed.n_adh100 / speed.n) if speed.n else None,
        },
        "peak": _band("peak"),
        "offpeak": _band("offpeak"),
        "hourly": [
            {"hour": r.hour, "dl_mean": _f(r.dl_mean), "n": r.n} for r in hourly
        ],
        "history": [
            {"t": r.timestamp.isoformat(), "dl": _f(r.download_mbps)} for r in history
        ],
        "loss": [
            {
                "target": r.target, "total": r.total, "fails": r.fails,
                "loss_pct": _f(100.0 * r.fails / r.total) if r.total else None,
            }
            for r in loss
        ],
        "outages": {
            "count": len(conn_events),
            "total_seconds": sum(durations),
            "longest_seconds": max(durations) if durations else 0,
            "events": conn_events,
            "host_events": host_events,
        },
        "degraded": {
            "count": len(deg),
            "total_seconds": sum(deg_durations),
            "longest_seconds": max(deg_durations) if deg_durations else 0,
            "worst_peak_pct": max(
                (d["peak_loss_pct"] for d in deg if d["peak_loss_pct"] is not None),
                default=None,
            ),
        },
    }


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
