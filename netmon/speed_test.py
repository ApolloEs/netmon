"""
Speed test runner — Layer 2 monitoring.

Samples current bandwidth, applies postpone/skip/force logic, runs the
Ookla CLI, and writes results to speed_tests + test_events.

Public API:
    run(engine, conf) — full cycle including retry loop; call once per
                        scheduled interval.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from typing import Optional

from sqlalchemy import insert
from sqlalchemy.engine import Engine

from netmon import config as cfg
from netmon.bandwidth import sample as sample_bandwidth
from netmon.db import speed_tests, test_events
from netmon.throughput import LoadContext, capture_load
from netmon.utils import now

log = logging.getLogger(__name__)

_SPEEDTEST_ARGS = ["--accept-license", "--accept-gdpr", "-f", "json"]


# ---------------------------------------------------------------------------
# DB writes
# ---------------------------------------------------------------------------

def _write_event(
    engine: Engine,
    status: str,
    scheduled_for=None,
    current_throughput_mbps: Optional[float] = None,
    reason: Optional[str] = None,
    retry_count: int = 0,
    speed_test_id: Optional[int] = None,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(test_events).values(
                timestamp=now(),
                status=status,
                scheduled_for=scheduled_for,
                current_throughput_mbps=current_throughput_mbps,
                reason=reason,
                retry_count=retry_count,
                speed_test_id=speed_test_id,
            )
        )


def _write_result(engine: Engine, data: dict, target_mbps: float, load: LoadContext) -> int:
    """Insert a speed_tests row and return its id."""
    try:
        dl = data["download"]["bandwidth"] * 8 / 1_000_000
        ul = data["upload"]["bandwidth"] * 8 / 1_000_000
        ping = data["ping"]["latency"]
        jitter = data["ping"]["jitter"]
    except KeyError as exc:
        raise RuntimeError(f"Unexpected speedtest JSON structure — missing key {exc}") from exc

    loss = data.get("packetLoss", 0.0)
    server = data.get("server", {})

    with engine.begin() as conn:
        result = conn.execute(
            insert(speed_tests)
            .values(
                timestamp=now(),
                download_mbps=dl,
                upload_mbps=ul,
                ping_ms=ping,
                jitter_ms=jitter,
                packet_loss_pct=loss,
                target_mbps=target_mbps,
                pct_of_target=round(dl / target_mbps * 100, 1),
                server_id=str(server.get("id", "")),
                server_name=server.get("name", ""),
                download_bytes=data["download"].get("bytes"),
                upload_bytes=data["upload"].get("bytes"),
                local_down_mbps=load.down_mbps,
                local_up_mbps=load.up_mbps,
                utilization_pct=load.utilization_pct,
                load_tier=load.tier,
            )
            .returning(speed_tests.c.id)
        )
        return result.scalar_one()


# ---------------------------------------------------------------------------
# Ookla CLI invocation
# ---------------------------------------------------------------------------

def _run_speedtest(cli_path: str) -> dict:
    """
    Invoke the Ookla CLI and return parsed JSON.
    Raises RuntimeError on non-zero exit or unparseable output.
    """
    try:
        proc = subprocess.run(
            [cli_path] + _SPEEDTEST_ARGS,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError:
        raise RuntimeError(f"speedtest CLI not found at '{cli_path}'")
    except subprocess.TimeoutExpired:
        raise RuntimeError("speedtest CLI timed out after 120s")

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(f"speedtest exited {proc.returncode}: {detail}")

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Could not parse speedtest output: {exc}\n{proc.stdout[:500]}")


# ---------------------------------------------------------------------------
# Threshold logic
# ---------------------------------------------------------------------------

def _check_thresholds(dl_mbps: float, target_mbps: float, conf: cfg.SpeedTestConfig) -> str:
    """Return 'proceed', 'postpone', or 'skip'."""
    ratio = dl_mbps / target_mbps
    if ratio > conf.hard_threshold:
        return "skip"
    if ratio > conf.soft_threshold:
        return "postpone"
    return "proceed"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Guards against concurrent runs (a manual dashboard-triggered test and a
# scheduled one are separate APScheduler jobs, so max_instances won't help).
_run_lock = threading.Lock()


def is_running() -> bool:
    """True while a speed test cycle is in progress."""
    return _run_lock.locked()


# A single silent retry cushions transient CLI failures (momentary DNS/SSL
# glitches, Ookla config-fetch hiccups) without masking real problems.
_RETRY_DELAY_SECONDS = 15


def _run_speedtest_with_retry(cli_path: str) -> dict:
    try:
        return _run_speedtest(cli_path)
    except RuntimeError as exc:
        log.warning(
            "Speed test attempt failed (%s) — retrying once in %ds...",
            exc, _RETRY_DELAY_SECONDS,
        )
        time.sleep(_RETRY_DELAY_SECONDS)
        return _run_speedtest(cli_path)


def run(
    engine: Engine,
    conf: cfg.Config,
    scheduled_for=None,
    force: bool = False,
    retry_count: int = 0,
    sampler=None,
) -> tuple[str, Optional[int]]:
    """
    One speed-test decision cycle. Does NOT sleep on postponement — the
    caller (jobs.speed_test_job) schedules a one-off retry job instead,
    passing the incremented retry_count back in, so no scheduler thread
    is ever parked for minutes.

    force=True (manual run from the dashboard) bypasses the postpone/skip
    decision entirely — bandwidth is still sampled for the event record.

    Returns (status, test_id): status is one of completed | forced |
    skipped | postponed | error | busy; test_id is set only when a test
    actually completed.
    """
    if not _run_lock.acquire(blocking=False):
        log.warning("Speed test already in progress — skipping this run.")
        return ("busy", None)
    try:
        return _run_cycle(engine, conf, scheduled_for, force, retry_count, sampler)
    finally:
        _run_lock.release()


def _run_cycle(
    engine: Engine, conf: cfg.Config, scheduled_for, force: bool, retry_count: int, sampler=None
) -> tuple[str, Optional[int]]:
    st_conf = conf.speed_test

    # Local host load just before the test (background usage), for annotating
    # the result row — captured before the Ookla run saturates the line.
    load = capture_load(
        sampler, conf.target_mbps,
        conf.monitoring.idle_ceiling_pct, conf.monitoring.light_ceiling_pct,
    )

    # Decide whether to run now. With measured host throughput the decision
    # is utilization-based — postpone while the line is loaded, force-run once
    # postpones are exhausted (a labelled, caveated measurement beats a blind
    # spot; there is no silent "skip"). Without a usable sample yet, fall back
    # to the legacy pre-test bandwidth sample + threshold check.
    if force:
        decision, dl_now = "proceed", load.down_mbps
    elif load.tier is not None:
        dl_now = load.down_mbps
        decision = "postpone" if load.tier == "loaded" else "proceed"
        log.info(
            "Local usage %.1f%% of contracted capacity (%s).",
            load.utilization_pct, load.tier,
        )
    else:
        log.info("Sampling bandwidth before speed test (host-throughput not measured)...")
        dl_now, _ = sample_bandwidth(interval_seconds=5)
        log.info("Current download: %.2f Mbps (target: %.0f Mbps)", dl_now, conf.target_mbps)
        decision = _check_thresholds(dl_now, conf.target_mbps, st_conf)

    if decision == "skip":  # legacy fallback path only
        log.warning(
            "Skipping speed test — current use %.2f Mbps exceeds hard threshold (%.0f%% of target).",
            dl_now, st_conf.hard_threshold * 100,
        )
        _write_event(
            engine, "skipped",
            scheduled_for=scheduled_for,
            current_throughput_mbps=dl_now,
            reason=f"current use {dl_now:.2f} Mbps > hard threshold",
            retry_count=retry_count,
        )
        return ("skipped", None)

    if decision == "postpone" and retry_count < st_conf.max_postpones:
        reason = (
            f"local usage {load.utilization_pct:.0f}% of capacity (loaded)"
            if load.tier is not None
            else f"current use {dl_now:.2f} Mbps > soft threshold"
        )
        log.info(
            "Postponing speed test — %s. Retry %d/%d in %d min.",
            reason, retry_count + 1, st_conf.max_postpones, st_conf.postpone_retry_minutes,
        )
        _write_event(
            engine, "postponed",
            scheduled_for=scheduled_for,
            current_throughput_mbps=dl_now,
            reason=reason,
            retry_count=retry_count,
        )
        return ("postponed", None)

    if decision == "postpone":
        log.warning("Forcing speed test after %d postpones — blind spot prevention.", retry_count)

    # "forced": manual run, or ran under load after exhausting postpones — a
    # compromised measurement, labelled so it is never published as clean.
    # "completed" for normal runs (idle/light, or max_postpones == 0).
    status = "forced" if (force or decision == "postpone") else "completed"
    if force:
        reason = "manual run from dashboard"
    elif decision == "postpone":
        reason = (
            f"forced after {retry_count} postpones under load "
            f"({load.tier}, {load.utilization_pct:.0f}% of capacity)"
            if load.tier is not None
            else f"forced after {retry_count} postpones"
        )
    else:
        reason = None
    log.info("Running speed test (status will be: %s)...", status)

    try:
        data = _run_speedtest_with_retry(st_conf.cli_path)
    except RuntimeError as exc:
        log.error("Speed test failed: %s", exc)
        _write_event(
            engine, "error",
            scheduled_for=scheduled_for,
            current_throughput_mbps=dl_now,
            reason=str(exc),
            retry_count=retry_count,
        )
        return ("error", None)

    test_id = _write_result(engine, data, conf.target_mbps, load)
    dl_result = data["download"]["bandwidth"] * 8 / 1_000_000
    log.info(
        "Speed test complete — %.1f Mbps down (%.0f%% of target). DB id: %d",
        dl_result, dl_result / conf.target_mbps * 100, test_id,
    )

    _write_event(
        engine, status,
        scheduled_for=scheduled_for,
        current_throughput_mbps=dl_now,
        reason=reason,
        retry_count=retry_count,
        speed_test_id=test_id,
    )
    return (status, test_id)
