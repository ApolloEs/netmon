"""
Unit tests for internet-facing interface resolution. subprocess and
psutil are mocked — no real network. Follows the canned-output pattern
from test_gateway_resolution.py.
"""

import subprocess
import sys
from types import SimpleNamespace

import psutil

from netmon import throughput

IP_ROUTE_DEFAULT = "default via 192.168.1.254 dev eth0 proto dhcp metric 100\n"
IP_ROUTE_WLAN = "default via 10.0.0.1 dev wlan0 proto dhcp metric 600\n"
IP_ROUTE_NO_DEV = "default via 192.168.1.254 proto dhcp\n"
IP_ROUTE_EMPTY = ""


def fake_run(stdout):
    def _run(cmd, **kwargs):
        return SimpleNamespace(stdout=stdout, returncode=0)
    return _run


def nic(address, netmask):
    # family AF_INET marks it as an IPv4 address entry.
    import socket
    return SimpleNamespace(family=socket.AF_INET, address=address, netmask=netmask)


# ---------------------------------------------------------------------------
# Linux: parse `dev` from the default route
# ---------------------------------------------------------------------------

def test_ip_route_names_device(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run(IP_ROUTE_DEFAULT))
    assert throughput._interface_from_ip_route() == "eth0"


def test_ip_route_wlan(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run(IP_ROUTE_WLAN))
    assert throughput._interface_from_ip_route() == "wlan0"


def test_ip_route_no_dev(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run(IP_ROUTE_NO_DEV))
    assert throughput._interface_from_ip_route() is None


# ---------------------------------------------------------------------------
# Gateway-subnet match (Windows path / portable fallback)
# ---------------------------------------------------------------------------

def test_interface_for_gateway_subnet_match(monkeypatch):
    monkeypatch.setattr(psutil, "net_if_addrs", lambda: {
        "Loopback": [nic("127.0.0.1", "255.0.0.0")],
        "Ethernet": [nic("192.168.2.10", "255.255.255.0")],
        "VMware": [nic("192.168.199.1", "255.255.255.0")],
    })
    assert throughput._interface_for_gateway("192.168.2.1") == "Ethernet"


def test_interface_for_gateway_no_match(monkeypatch):
    monkeypatch.setattr(psutil, "net_if_addrs", lambda: {
        "Ethernet": [nic("10.5.0.10", "255.255.255.0")],
    })
    assert throughput._interface_for_gateway("192.168.2.1") is None


# ---------------------------------------------------------------------------
# resolve_interface — dispatch, override, disable, validation
# ---------------------------------------------------------------------------

def _counters(*names):
    return lambda pernic=True: {n: SimpleNamespace() for n in names}


def test_auto_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(subprocess, "run", fake_run(IP_ROUTE_DEFAULT))
    monkeypatch.setattr(psutil, "net_io_counters", _counters("eth0", "lo"))
    assert throughput.resolve_interface("auto") == "eth0"


def test_auto_windows_via_gateway(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(throughput.pinger, "_resolve_gateway", lambda: "192.168.2.1")
    monkeypatch.setattr(psutil, "net_if_addrs", lambda: {
        "Ethernet": [nic("192.168.2.10", "255.255.255.0")],
    })
    monkeypatch.setattr(psutil, "net_io_counters", _counters("Ethernet", "Wi-Fi"))
    assert throughput.resolve_interface("auto") == "Ethernet"


def test_explicit_override_present(monkeypatch):
    monkeypatch.setattr(psutil, "net_io_counters", _counters("eth0", "eth1"))
    assert throughput.resolve_interface("eth1") == "eth1"


def test_explicit_override_absent_disables(monkeypatch):
    monkeypatch.setattr(psutil, "net_io_counters", _counters("eth0"))
    assert throughput.resolve_interface("wlan9") is None


def test_explicit_none_disables(monkeypatch):
    monkeypatch.setattr(psutil, "net_io_counters", _counters("eth0"))
    assert throughput.resolve_interface("none") is None


def test_auto_detected_but_no_counters_disables(monkeypatch):
    # Route names a device that psutil can't report counters for.
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(subprocess, "run", fake_run(IP_ROUTE_DEFAULT))
    monkeypatch.setattr(psutil, "net_io_counters", _counters("lo"))
    assert throughput.resolve_interface("auto") is None


def test_auto_failure_disables(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")

    def boom(cmd, **kwargs):
        raise FileNotFoundError("no ip command")
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(throughput.pinger, "_resolve_gateway", lambda: None)
    monkeypatch.setattr(psutil, "net_io_counters", _counters("eth0"))
    assert throughput.resolve_interface("auto") is None
