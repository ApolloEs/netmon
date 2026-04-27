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
import time
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import insert
from sqlalchemy.engine import Engine

from netmon import config as cfg
from netmon.bandwidth import sample as sample_bandwidth
from netmon.db import speed_tests, test_events

log = logging.getLogger(__name__)

_SPEEDTEST_ARGS = ["--accept-license", "--accept-gdpr", "-f", "json"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# DB writes
# ---------------------------------------------------------------------------

def _write_event(
    engine: Engine,
    status: str,
    scheduled_for: Optional[datetime] = None,
    current_throughput_mbps: Optional[float] = None,
    reason: Optional[str] = None,
    retry_count: int = 0,
    speed_test_id: Optional[int] = None,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(test_events).values(
                timestamp=_now(),
                status=status,
                scheduled_for=scheduled_for,
                current_throughput_mbps=current_throughput_mbps,
                reason=reason,
                retry_count=retry_count,
                speed_test_id=speed_test_id,
            )
        )


def _write_result(engine: Engine, data: dict, target_mbps: float) -> int:
    """Insert a speed_tests row and return its id."""
    dl = data["download"]["bandwidth"] * 8 / 1_000_000
    ul = data["upload"]["bandwidth"] * 8 / 1_000_000
    ping = data["ping"]["latency"]
    jitter = data["ping"]["jitter"]
    loss = data.get("packetLoss", 0.0)
    server = data.get("server", {})

    with engine.begin() as conn:
        result = conn.execute(
            insert(speed_tests)
            .values(
                timestamp=_now(),
                download_mbps=dl,
                upload_mbps=ul,
                ping_ms=ping,
                jitter_ms=jitter,
                packet_loss_pct=loss,
                target_mbps=target_mbps,
                pct_of_target=round(dl / target_mbps * 100, 1),
                server_id=str(server.get("id", "")),
                server_name=server.get("name", ""),
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
        raise RuntimeError(f"speedtest exited {proc.returncode}: {proc.stderr.strip()}")

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Could not parse speedtest output: {exc}\n{proc.stdout[:500]}")


# ---------------------------------------------------------------------------
# Threshold logic
# ---------------------------------------------------------------------------

def _check_thresholds(
    dl_mbps: float,
    target_mbps: float,
    conf: cfg.SpeedTestConfig,
) -> str:
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

def run(engine: Engine, conf: cfg.Config, scheduled_for: Optional[datetime] = None) -> None:
    """
    Full speed-test cycle with postpone/skip/force logic.
    Blocks until the test completes, is skipped, or is forced after
    max_postpones consecutive postponements.
    """
    st_conf = conf.speed_test
    retry_count = 0

    while True:
        log.info("Sampling bandwidth before speed test...")
        dl_now, _ = sample_bandwidth(interval_seconds=5)
        log.info("Current download: %.2f Mbps (target: %.0f Mbps)", dl_now, conf.target_mbps)

        decision = _check_thresholds(dl_now, conf.target_mbps, st_conf)

        if decision == "skip":
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
            return

        if decision == "postpone" and retry_count < st_conf.max_postpones:
            log.info(
                "Postponing speed test — current use %.2f Mbps exceeds soft threshold. "
                "Retry %d/%d in %d min.",
                dl_now, retry_count + 1, st_conf.max_postpones, st_conf.postpone_retry_minutes,
            )
            _write_event(
                engine, "postponed",
                scheduled_for=scheduled_for,
                current_throughput_mbps=dl_now,
                reason=f"current use {dl_now:.2f} Mbps > soft threshold",
                retry_count=retry_count,
            )
            retry_count += 1
            time.sleep(st_conf.postpone_retry_minutes * 60)
            continue

        if decision == "postpone" and retry_count >= st_conf.max_postpones:
            log.warning(
                "Forcing speed test after %d postpones — blind spot prevention.",
                retry_count,
            )

        # Proceed (or forced)
        status = "forced" if retry_count >= st_conf.max_postpones else "completed"
        log.info("Running speed test (status will be: %s)...", status)

        try:
            data = _run_speedtest(st_conf.cli_path)
        except RuntimeError as exc:
            log.error("Speed test failed: %s", exc)
            _write_event(
                engine, "error",
                scheduled_for=scheduled_for,
                current_throughput_mbps=dl_now,
                reason=str(exc),
                retry_count=retry_count,
            )
            return

        test_id = _write_result(engine, data, conf.target_mbps)
        dl_result = data["download"]["bandwidth"] * 8 / 1_000_000
        log.info(
            "Speed test complete — %.1f Mbps down (%.0f%% of target). DB id: %d",
            dl_result, dl_result / conf.target_mbps * 100, test_id,
        )

        _write_event(
            engine, status,
            scheduled_for=scheduled_for,
            current_throughput_mbps=dl_now,
            reason=None,
            retry_count=retry_count,
            speed_test_id=test_id,
        )
        return
