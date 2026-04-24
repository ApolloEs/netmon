"""
Connectivity pinger — Layer 1 monitoring.

Pings anchor hosts (1.1.1.1, 8.8.8.8, local gateway) on a tight loop.
Writes every result to connectivity_pings and maintains open/closed
records in outages.

Public API:
    run_once(engine, conf)   — one full ping cycle across all targets
    run_loop(engine, conf)   — blocks forever, sleeping ping_interval_seconds
"""

from __future__ import annotations

import logging
import socket
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, Optional

import psutil
from icmplib import ping as icmp_ping
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

from netmon import config as cfg
from netmon.db import connectivity_pings, outages

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Gateway resolution
# ---------------------------------------------------------------------------

def _resolve_gateway() -> Optional[str]:
    """Return the default gateway IP, or None if it can't be determined."""
    try:
        # psutil.net_if_stats gives interface info but not routes.
        # Use the socket trick: connect to a public IP (no packet sent)
        # and read the local address — then map that to the gateway via
        # psutil.net_if_addrs + the AF_INET default route heuristic.
        #
        # On Windows, `psutil` doesn't expose the routing table directly.
        # The most reliable cross-platform approach without extra libs:
        # open a UDP socket toward 8.8.8.8 and read the outbound interface,
        # then look up the gateway for that interface via net_if_stats.
        # Fallback: parse `ipconfig` output.
        import subprocess
        result = subprocess.run(
            ["ipconfig"],
            capture_output=True, text=True, timeout=5
        )
        # "Default Gateway" may span multiple lines (IPv6 first, IPv4 on a
        # continuation line).  Collect all candidate IPs from the gateway
        # block and return the first plain IPv4 address found.
        import re
        in_gateway = False
        ipv4_re = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
        for line in result.stdout.splitlines():
            if "Default Gateway" in line:
                in_gateway = True
                # Check the same line first (value after last colon).
                candidate = line.rsplit(":", 1)[-1].strip()
                if ipv4_re.match(candidate):
                    return candidate
            elif in_gateway:
                stripped = line.strip()
                if not stripped:
                    in_gateway = False
                    continue
                # Continuation lines are indented and contain just the value.
                if ipv4_re.match(stripped):
                    return stripped
                # If it's a new label line, we've left the gateway block.
                if ":" in stripped and not stripped.startswith("2") and not stripped.startswith("fe"):
                    in_gateway = False
        return None
    except Exception as exc:
        log.warning("Could not resolve gateway: %s", exc)
        return None


def resolve_targets(raw_targets: list[str]) -> list[str]:
    """Replace 'gateway' placeholder with the actual gateway IP."""
    resolved = []
    for t in raw_targets:
        if t.lower() == "gateway":
            gw = _resolve_gateway()
            if gw:
                log.info("Resolved gateway → %s", gw)
                resolved.append(gw)
            else:
                log.warning("Could not resolve gateway; skipping that target.")
        else:
            resolved.append(t)
    return resolved


# ---------------------------------------------------------------------------
# Outage state (in-memory, per process lifetime)
# ---------------------------------------------------------------------------

# Maps target → consecutive failure count
_fail_streak: Dict[str, int] = defaultdict(int)

# Maps target → outage row id (non-None means an outage is open for that target)
_open_outage: Dict[str, Optional[int]] = defaultdict(lambda: None)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _record_ping(engine: Engine, target: str, success: bool, latency_ms: Optional[float]) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(connectivity_pings).values(
                timestamp=_now(),
                target=target,
                success=success,
                latency_ms=latency_ms,
            )
        )


def _open_outage_record(engine: Engine, target: str) -> int:
    with engine.begin() as conn:
        result = conn.execute(
            insert(outages)
            .values(started_at=_now(), trigger=target)
            .returning(outages.c.id)
        )
        return result.scalar_one()


def _close_outage_record(engine: Engine, outage_id: int) -> None:
    now = _now()
    with engine.begin() as conn:
        row = conn.execute(
            select(outages.c.started_at).where(outages.c.id == outage_id)
        ).one()
        duration = int((now - row.started_at).total_seconds())
        conn.execute(
            update(outages)
            .where(outages.c.id == outage_id)
            .values(ended_at=now, duration_seconds=duration)
        )
    log.info("Outage #%d closed — duration %ds", outage_id, duration)


# ---------------------------------------------------------------------------
# Core ping logic
# ---------------------------------------------------------------------------

def _ping_target(target: str) -> tuple[bool, Optional[float]]:
    """Ping once. Returns (success, latency_ms)."""
    try:
        result = icmp_ping(target, count=1, timeout=2, privileged=False)
        if result.is_alive:
            return True, result.avg_rtt
        return False, None
    except Exception as exc:
        log.debug("Ping error for %s: %s", target, exc)
        return False, None


def _handle_result(
    engine: Engine,
    target: str,
    success: bool,
    latency_ms: Optional[float],
    outage_threshold: int,
) -> None:
    _record_ping(engine, target, success, latency_ms)

    if success:
        if _fail_streak[target] > 0:
            log.info("%-15s  recovered after %d failures", target, _fail_streak[target])
        _fail_streak[target] = 0
        if _open_outage[target] is not None:
            _close_outage_record(engine, _open_outage[target])
            _open_outage[target] = None
    else:
        _fail_streak[target] += 1
        log.warning("%-15s  FAIL  (streak: %d)", target, _fail_streak[target])
        if (
            _fail_streak[target] >= outage_threshold
            and _open_outage[target] is None
        ):
            outage_id = _open_outage_record(engine, target)
            _open_outage[target] = outage_id
            log.warning("Outage #%d opened — trigger: %s", outage_id, target)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_once(engine: Engine, conf: cfg.Config, targets: list[str]) -> None:
    """Ping all targets once and persist results."""
    threshold = conf.connectivity.outage_threshold_failures
    for target in targets:
        success, latency_ms = _ping_target(target)
        status = f"{latency_ms:.1f}ms" if latency_ms is not None else "FAIL"
        log.info("%-15s  %s", target, status)
        _handle_result(engine, target, success, latency_ms, threshold)


def run_loop(engine: Engine, conf: cfg.Config) -> None:
    """Block forever, running one ping cycle per ping_interval_seconds."""
    targets = resolve_targets(conf.connectivity.ping_targets)
    if not targets:
        log.error("No ping targets resolved — aborting pinger.")
        return

    interval = conf.connectivity.ping_interval_seconds
    log.info("Pinger started. Targets: %s  Interval: %ds", targets, interval)

    while True:
        run_once(engine, conf, targets)
        time.sleep(interval)
