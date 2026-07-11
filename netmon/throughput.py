"""
Host throughput monitoring — Layer 3.

Samples how much of the local machine's own bandwidth is in use, so every
speed test / outage / degraded period can be annotated with local load as
a fraction of contracted capacity. This is what makes the evidence
un-dismissable: "throughput was 8 Mbps while local usage was under 1% of
the contracted 100 Mbps."

Correctness rule: the interface must be the one carrying the default
route. Picking wrong (a bridge, loopback, or idle NIC) would silently
report ~0 Mbps forever and make every report falsely claim "measured
while idle" — a false evidentiary claim. So detection derives the
interface from the default route and, if it can't, the feature is
DISABLED (annotations absent) rather than guessing.

Public API (Phase 1):
    resolve_interface(configured) -> str | None
"""

from __future__ import annotations

import ipaddress
import logging
import subprocess
import sys
import time
from collections import deque
from typing import Optional

import psutil
from sqlalchemy import insert
from sqlalchemy.engine import Engine

from netmon import pinger
from netmon.db import host_throughput
from netmon.utils import now

log = logging.getLogger(__name__)

# Keep ~5 minutes of samples so any event can ask "what was local usage
# around this time" without a DB round-trip.
_WINDOW_SECONDS = 300


def _interface_names() -> set[str]:
    """Interfaces psutil can actually report byte counters for."""
    return set(psutil.net_io_counters(pernic=True).keys())


def _interface_from_ip_route() -> Optional[str]:
    """Linux: the `dev <iface>` field of the default route."""
    result = subprocess.run(
        ["ip", "route", "show", "default"], capture_output=True, text=True, timeout=5
    )
    for line in result.stdout.splitlines():
        parts = line.split()
        if "dev" in parts:
            return parts[parts.index("dev") + 1]
    return None


def _interface_for_gateway(gateway_ip: str) -> Optional[str]:
    """
    Find the interface whose IPv4 subnet contains the gateway. Used on
    Windows (and as a portable fallback), since psutil's adapter names
    don't line up with `ipconfig`'s section headers.
    """
    try:
        gw = ipaddress.ip_address(gateway_ip)
    except ValueError:
        return None
    for name, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family != psutil.AF_LINK and addr.netmask and "." in str(addr.address):
                try:
                    net = ipaddress.ip_network(
                        f"{addr.address}/{addr.netmask}", strict=False
                    )
                except ValueError:
                    continue
                if gw in net:
                    return name
    return None


def _auto_interface() -> Optional[str]:
    """Derive the internet-facing interface from the default route."""
    if sys.platform != "win32":
        iface = _interface_from_ip_route()
        if iface:
            return iface
    # Windows, or a Linux box where `ip route` didn't name a device:
    # locate the adapter on the gateway's subnet.
    gateway = pinger._resolve_gateway()
    if gateway:
        return _interface_for_gateway(gateway)
    return None


def resolve_interface(configured: str) -> Optional[str]:
    """
    Return the interface to monitor, or None to disable the feature.

    `configured` is `monitoring.interface`: "auto" derives it from the
    default route; any other value is an explicit override. Either way the
    result must exist in psutil's counter map, or we return None (disabled)
    rather than annotate with a wrong or absent interface.
    """
    available = _interface_names()
    configured = (configured or "auto").strip()

    # Explicit disable — e.g. inside Docker, where the container can only
    # see its own traffic, not the host's, so "idle" would be misleading.
    if configured.lower() in ("", "none", "off", "disabled"):
        return None

    if configured.lower() != "auto":
        if configured in available:
            return configured
        log.warning(
            "Configured monitoring.interface '%s' not found (available: %s); "
            "host-throughput disabled.",
            configured, ", ".join(sorted(available)) or "none",
        )
        return None

    try:
        iface = _auto_interface()
    except Exception as exc:
        log.warning("Interface auto-detection failed: %s", exc)
        iface = None

    if iface and iface in available:
        return iface
    if iface:
        log.warning(
            "Auto-detected interface '%s' has no byte counters; host-throughput disabled.",
            iface,
        )
    return None


def _bytes_to_mbps(byte_count: int, seconds: float) -> float:
    return (byte_count * 8) / (seconds * 1_000_000)


class ThroughputSampler:
    """
    Turns cumulative NIC byte counters into per-interval Mbps and keeps a
    short rolling window. Pure bookkeeping: `poll` takes the current
    counters and clock so it can be unit-tested without psutil or sleeping.

    The 5-second scheduler cadence IS the sampling interval — the sampler
    never sleeps (that would park an executor worker). Each poll diffs
    against the previous snapshot and divides by the MEASURED elapsed time.
    """

    def __init__(self, interface: str):
        self.interface = interface
        self._prev: Optional[tuple[float, int, int]] = None  # (ts, recv, sent)
        # (ts, down_mbps, up_mbps), newest last
        self.window: deque[tuple[float, float, float]] = deque()

    def poll(self, ts: float, recv_bytes: int, sent_bytes: int) -> Optional[tuple[float, float]]:
        """
        Record a counter reading. Returns (down_mbps, up_mbps) for the
        interval since the previous reading, or None when no rate can be
        computed yet (first reading, zero elapsed, or a counter reset).
        """
        prev = self._prev
        self._prev = (ts, recv_bytes, sent_bytes)
        if prev is None:
            return None
        elapsed = ts - prev[0]
        if elapsed <= 0:
            return None
        down_delta = recv_bytes - prev[1]
        up_delta = sent_bytes - prev[2]
        if down_delta < 0 or up_delta < 0:
            # Counter reset (interface restart, sleep/resume) — an impossible
            # negative rate. Skip this sample; the next one measures cleanly.
            log.debug("Counter reset on %s; skipping sample.", self.interface)
            return None
        down = _bytes_to_mbps(down_delta, elapsed)
        up = _bytes_to_mbps(up_delta, elapsed)
        self.window.append((ts, down, up))
        self._trim(ts)
        return down, up

    def _trim(self, now_ts: float) -> None:
        cutoff = now_ts - _WINDOW_SECONDS
        while self.window and self.window[0][0] < cutoff:
            self.window.popleft()

    def latest_down_mbps(self) -> Optional[float]:
        """Most recent measured download rate, or None if none yet."""
        return self.window[-1][1] if self.window else None

    def latest_up_mbps(self) -> Optional[float]:
        return self.window[-1][2] if self.window else None

    def recent_utilization(self, contracted_down_mbps: float) -> Optional[float]:
        """Latest local download as a percent of contracted capacity."""
        down = self.latest_down_mbps()
        if down is None or contracted_down_mbps <= 0:
            return None
        return down / contracted_down_mbps * 100.0


def load_tier(util_pct: Optional[float], idle_ceiling_pct: float, light_ceiling_pct: float) -> Optional[str]:
    """
    Bucket a utilization percentage into idle / light / loaded, or None
    when load wasn't measured (so callers never imply "idle" by default).
    """
    if util_pct is None:
        return None
    if util_pct < idle_ceiling_pct:
        return "idle"
    if util_pct < light_ceiling_pct:
        return "light"
    return "loaded"


def read_counters(interface: str) -> Optional[tuple[float, int, int]]:
    """Current (wall_ts, bytes_recv, bytes_sent) for the interface, or None."""
    counters = psutil.net_io_counters(pernic=True).get(interface)
    if counters is None:
        return None
    return time.time(), counters.bytes_recv, counters.bytes_sent


def record_sample(engine: Engine, interface: str, down_mbps: float, up_mbps: float) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(host_throughput).values(
                timestamp=now(),
                interface=interface,
                down_mbps=down_mbps,
                up_mbps=up_mbps,
            )
        )
