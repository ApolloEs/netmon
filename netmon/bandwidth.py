"""
Bandwidth sampler — measures current interface throughput via psutil.

Used by the speed test runner to decide whether to proceed, postpone,
or skip a test based on how much of the connection is already in use.

Public API:
    sample(interval_seconds) -> (download_mbps, upload_mbps)
"""

from __future__ import annotations

import logging
import time

import psutil

log = logging.getLogger(__name__)


def _bytes_to_mbps(byte_count: int, interval: float) -> float:
    return (byte_count * 8) / (interval * 1_000_000)


def _best_interface() -> str | None:
    """
    Return the name of the interface with the most cumulative traffic,
    excluding loopback. This is a proxy for the active WAN interface.
    """
    stats = psutil.net_io_counters(pernic=True)
    best = None
    best_total = -1
    for name, counters in stats.items():
        if name.lower().startswith("lo"):
            continue
        total = counters.bytes_sent + counters.bytes_recv
        if total > best_total:
            best_total = total
            best = name
    return best


def sample(interval_seconds: float = 5.0) -> tuple[float, float]:
    """
    Sample throughput over `interval_seconds`. Returns (download_mbps, upload_mbps).
    Raises RuntimeError if no usable network interface is found.
    """
    iface = _best_interface()
    if iface is None:
        raise RuntimeError("No usable network interface found.")

    before = psutil.net_io_counters(pernic=True)
    if iface not in before:
        raise RuntimeError(f"Interface '{iface}' disappeared before sampling.")

    time.sleep(interval_seconds)

    after = psutil.net_io_counters(pernic=True)
    if iface not in after:
        # Interface went away mid-sample — return zeros so the caller can
        # proceed conservatively rather than crashing.
        log.warning("Interface '%s' disappeared during sampling; returning 0 Mbps.", iface)
        return 0.0, 0.0

    # Guard against counter resets (interface restart resets counters to 0).
    dl_bytes = max(0, after[iface].bytes_recv - before[iface].bytes_recv)
    ul_bytes = max(0, after[iface].bytes_sent - before[iface].bytes_sent)

    dl = _bytes_to_mbps(dl_bytes, interval_seconds)
    ul = _bytes_to_mbps(ul_bytes, interval_seconds)
    return dl, ul
