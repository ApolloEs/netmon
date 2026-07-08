"""
Unit tests for cross-platform gateway resolution and the concurrent ping
cycle. subprocess.run is mocked with canned command output — no network.
"""

import subprocess
import sys
from types import SimpleNamespace

from netmon import pinger
from netmon.pinger import PingerState

IPCONFIG_SIMPLE = """
Ethernet adapter Ethernet:

   Connection-specific DNS Suffix  . : lan
   IPv4 Address. . . . . . . . . . . : 192.168.2.10
   Subnet Mask . . . . . . . . . . . : 255.255.255.0
   Default Gateway . . . . . . . . . : 192.168.2.1
"""

# IPv6 gateway listed first; the IPv4 one continues on the next line.
IPCONFIG_IPV6_FIRST = """
Ethernet adapter Ethernet:

   IPv4 Address. . . . . . . . . . . : 192.168.2.10
   Default Gateway . . . . . . . . . : fe80::1%12
                                       192.168.2.1

Wireless LAN adapter Wi-Fi:

   Media State . . . . . . . . . . . : Media disconnected
"""

IPCONFIG_NO_GATEWAY = """
Ethernet adapter Ethernet:

   Media State . . . . . . . . . . . : Media disconnected
   Default Gateway . . . . . . . . . :
"""

IP_ROUTE_DEFAULT = "default via 192.168.1.254 dev eth0 proto dhcp metric 100\n"
IP_ROUTE_MULTI = (
    "default via 10.0.0.1 dev wlan0 proto dhcp metric 600\n"
    "default via 10.0.0.2 dev eth0 proto dhcp metric 100\n"
)
IP_ROUTE_EMPTY = ""


def fake_run(stdout):
    def _run(cmd, **kwargs):
        return SimpleNamespace(stdout=stdout, returncode=0)
    return _run


def test_ipconfig_simple(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run(IPCONFIG_SIMPLE))
    assert pinger._gateway_from_ipconfig() == "192.168.2.1"


def test_ipconfig_ipv6_listed_first(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run(IPCONFIG_IPV6_FIRST))
    assert pinger._gateway_from_ipconfig() == "192.168.2.1"


def test_ipconfig_no_gateway(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run(IPCONFIG_NO_GATEWAY))
    assert pinger._gateway_from_ipconfig() is None


def test_ip_route_default(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run(IP_ROUTE_DEFAULT))
    assert pinger._gateway_from_ip_route() == "192.168.1.254"


def test_ip_route_multiple_defaults_takes_first(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run(IP_ROUTE_MULTI))
    assert pinger._gateway_from_ip_route() == "10.0.0.1"


def test_ip_route_no_default(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run(IP_ROUTE_EMPTY))
    assert pinger._gateway_from_ip_route() is None


def test_resolve_gateway_dispatches_by_platform(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(subprocess, "run", fake_run(IP_ROUTE_DEFAULT))
    assert pinger._resolve_gateway() == "192.168.1.254"

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "run", fake_run(IPCONFIG_SIMPLE))
    assert pinger._resolve_gateway() == "192.168.2.1"


def test_resolve_gateway_swallows_command_failure(monkeypatch):
    def boom(cmd, **kwargs):
        raise FileNotFoundError("no such command")
    monkeypatch.setattr(subprocess, "run", boom)
    assert pinger._resolve_gateway() is None


def test_resolve_targets_substitutes_gateway(monkeypatch):
    monkeypatch.setattr(pinger, "_resolve_gateway", lambda: "192.168.2.1")
    assert pinger.resolve_targets(["1.1.1.1", "gateway"]) == ["1.1.1.1", "192.168.2.1"]


def test_resolve_targets_drops_unresolvable_gateway(monkeypatch):
    monkeypatch.setattr(pinger, "_resolve_gateway", lambda: None)
    assert pinger.resolve_targets(["1.1.1.1", "gateway"]) == ["1.1.1.1"]


# ---------------------------------------------------------------------------
# Concurrent ping cycle stays deterministic
# ---------------------------------------------------------------------------

def test_run_once_handles_results_in_target_order(monkeypatch):
    latency = {"1.1.1.1": 40.0, "8.8.8.8": None, "192.168.2.1": 1.0}
    monkeypatch.setattr(
        pinger, "_ping_target",
        lambda t: (latency[t] is not None, latency[t]),
    )
    handled = []
    monkeypatch.setattr(
        pinger, "_handle_result",
        lambda engine, state, target, success, lat, thr: handled.append((target, success)),
    )
    conf = SimpleNamespace(connectivity=SimpleNamespace(outage_threshold_failures=3))
    targets = ["1.1.1.1", "8.8.8.8", "192.168.2.1"]

    pinger.run_once(None, conf, targets, PingerState())

    assert handled == [("1.1.1.1", True), ("8.8.8.8", False), ("192.168.2.1", True)]
