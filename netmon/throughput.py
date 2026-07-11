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
from typing import Optional

import psutil

from netmon import pinger

log = logging.getLogger(__name__)


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
