"""
Connectivity pinger — Layer 1 monitoring.

Pings anchor hosts (1.1.1.1, 8.8.8.8, local gateway) on a tight loop.
Writes every result to connectivity_pings and maintains open/closed
records in outages.

Public API:
    PingerState                            — per-target streak/outage bookkeeping
    restore_state(engine, targets)         — rebuild a PingerState from the DB
    run_once(engine, conf, targets, state) — one full ping cycle across all targets
    run_loop(engine, conf)                 — blocks forever, sleeping ping_interval_seconds
"""

from __future__ import annotations

import logging
import subprocess
import time
from collections import defaultdict
from typing import Dict, Optional

from icmplib import ping as icmp_ping
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

from netmon import config as cfg
from netmon.db import connectivity_pings, outages
from netmon.utils import IPV4_RE, now

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Gateway resolution
# ---------------------------------------------------------------------------


def _resolve_gateway() -> Optional[str]:
    """Return the default IPv4 gateway, or None if it can't be determined."""
    try:
        result = subprocess.run(
            ["ipconfig"], capture_output=True, text=True, timeout=5
        )
        in_gateway = False
        for line in result.stdout.splitlines():
            if "Default Gateway" in line:
                in_gateway = True
                candidate = line.rsplit(":", 1)[-1].strip()
                if IPV4_RE.match(candidate):
                    return candidate
            elif in_gateway:
                stripped = line.strip()
                if not stripped:
                    in_gateway = False
                    continue
                if IPV4_RE.match(stripped):
                    return stripped
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
# In-memory outage state
# ---------------------------------------------------------------------------

class PingerState:
    """
    Per-target consecutive-failure streaks and open outage row ids.

    Pure bookkeeping: the transition methods return what the caller should
    do (close or open an outage record) but never touch the database —
    that separation is what makes the streak logic unit-testable.
    """

    def __init__(self) -> None:
        # target → consecutive failure count
        self.fail_streak: Dict[str, int] = defaultdict(int)
        # target → open outage row id (None = no active outage)
        self.open_outage: Dict[str, Optional[int]] = defaultdict(lambda: None)

    def record_success(self, target: str) -> tuple[int, Optional[int]]:
        """Reset the streak. Returns (previous_streak, outage_id_to_close)."""
        prev = self.fail_streak[target]
        self.fail_streak[target] = 0
        to_close = self.open_outage[target]
        self.open_outage[target] = None
        return prev, to_close

    def record_failure(self, target: str, outage_threshold: int) -> tuple[int, bool]:
        """Bump the streak. Returns (streak, whether an outage should open)."""
        self.fail_streak[target] += 1
        streak = self.fail_streak[target]
        should_open = streak >= outage_threshold and self.open_outage[target] is None
        return streak, should_open

    def outage_opened(self, target: str, outage_id: int) -> None:
        self.open_outage[target] = outage_id


def restore_state(engine: Engine, targets: list[str]) -> PingerState:
    """
    Rebuild pinger state from the DB after a process restart.
    Without this, a restart mid-outage would lose track of the open record
    and need to accumulate a full new failure streak before re-detecting.
    """
    state = PingerState()
    with engine.connect() as conn:
        open_rows = conn.execute(
            select(outages.c.id, outages.c.trigger)
            .where(outages.c.ended_at.is_(None))
            .where(outages.c.trigger.in_(targets))
        ).fetchall()
        for row in open_rows:
            state.open_outage[row.trigger] = row.id
            log.info("Restored open outage #%d for %s", row.id, row.trigger)

        for target in targets:
            recent = conn.execute(
                select(connectivity_pings.c.success)
                .where(connectivity_pings.c.target == target)
                .order_by(connectivity_pings.c.timestamp.desc())
                .limit(50)
            ).fetchall()
            streak = 0
            for row in recent:
                if not row.success:
                    streak += 1
                else:
                    break
            if streak:
                state.fail_streak[target] = streak
                log.info("Restored failure streak %d for %s", streak, target)
    return state


# ---------------------------------------------------------------------------
# DB writes
# ---------------------------------------------------------------------------

def _record_ping(engine: Engine, target: str, success: bool, latency_ms: Optional[float]) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(connectivity_pings).values(
                timestamp=now(),
                target=target,
                success=success,
                latency_ms=latency_ms,
            )
        )


def _open_outage_record(engine: Engine, target: str) -> int:
    with engine.begin() as conn:
        result = conn.execute(
            insert(outages)
            .values(started_at=now(), trigger=target)
            .returning(outages.c.id)
        )
        return result.scalar_one()


def _close_outage_record(engine: Engine, outage_id: int) -> None:
    closed_at = now()
    with engine.begin() as conn:
        row = conn.execute(
            select(outages.c.started_at).where(outages.c.id == outage_id)
        ).one()
        duration = int((closed_at - row.started_at).total_seconds())
        conn.execute(
            update(outages)
            .where(outages.c.id == outage_id)
            .values(ended_at=closed_at, duration_seconds=duration)
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
    state: PingerState,
    target: str,
    success: bool,
    latency_ms: Optional[float],
    outage_threshold: int,
) -> None:
    _record_ping(engine, target, success, latency_ms)

    if success:
        prev_streak, to_close = state.record_success(target)
        if prev_streak > 0:
            log.info("%-15s  recovered after %d failures", target, prev_streak)
        if to_close is not None:
            _close_outage_record(engine, to_close)
    else:
        streak, should_open = state.record_failure(target, outage_threshold)
        log.warning("%-15s  FAIL  (streak: %d)", target, streak)
        if should_open:
            outage_id = _open_outage_record(engine, target)
            state.outage_opened(target, outage_id)
            log.warning("Outage #%d opened — trigger: %s", outage_id, target)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_once(engine: Engine, conf: cfg.Config, targets: list[str], state: PingerState) -> None:
    """Ping all targets once and persist results."""
    threshold = conf.connectivity.outage_threshold_failures
    for target in targets:
        success, latency_ms = _ping_target(target)
        status = f"{latency_ms:.1f}ms" if latency_ms is not None else "FAIL"
        log.info("%-15s  %s", target, status)
        _handle_result(engine, state, target, success, latency_ms, threshold)


def run_loop(engine: Engine, conf: cfg.Config) -> None:
    """Block forever, running one ping cycle per ping_interval_seconds."""
    targets = resolve_targets(conf.connectivity.ping_targets)
    if not targets:
        log.error("No ping targets resolved — aborting pinger.")
        return

    interval = conf.connectivity.ping_interval_seconds
    log.info("Pinger started. Targets: %s  Interval: %ds", targets, interval)

    state = restore_state(engine, targets)

    while True:
        run_once(engine, conf, targets, state)
        time.sleep(interval)
