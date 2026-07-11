"""Unit tests for ThroughputSampler — pure counter-diff bookkeeping, no psutil."""

import pytest

from netmon.throughput import ThroughputSampler, load_tier

MB = 1_000_000  # bytes; 1 MB/s over 1s = 8 Mbps


def test_first_poll_returns_none():
    s = ThroughputSampler("eth0")
    assert s.poll(1000.0, 0, 0) is None
    assert len(s.window) == 0


def test_rate_from_counter_delta():
    s = ThroughputSampler("eth0")
    s.poll(1000.0, 0, 0)
    # +1 MB down, +0.5 MB up over exactly 1 second → 8 / 4 Mbps.
    down, up = s.poll(1001.0, 1 * MB, MB // 2)
    assert round(down, 3) == 8.0
    assert round(up, 3) == 4.0


def test_uses_measured_elapsed_not_assumed_interval():
    s = ThroughputSampler("eth0")
    s.poll(1000.0, 0, 0)
    # Same 1 MB but over 2 seconds → half the rate.
    down, _ = s.poll(1002.0, 1 * MB, 0)
    assert round(down, 3) == 4.0


def test_zero_elapsed_skipped():
    s = ThroughputSampler("eth0")
    s.poll(1000.0, 0, 0)
    assert s.poll(1000.0, 5 * MB, 0) is None


def test_counter_reset_skipped():
    s = ThroughputSampler("eth0")
    s.poll(1000.0, 10 * MB, 10 * MB)
    # Counters dropped (interface restart) → negative delta → skip.
    assert s.poll(1001.0, 1 * MB, 1 * MB) is None
    assert len(s.window) == 0
    # Next clean interval measures normally against the reset baseline.
    down, _ = s.poll(1002.0, 2 * MB, 1 * MB)
    assert round(down, 3) == 8.0


def test_window_accumulates_and_trims():
    s = ThroughputSampler("eth0")
    s.poll(0.0, 0, 0)
    # Samples 1s apart across more than the 5-min window.
    for i in range(1, 400):
        s.poll(float(i), i * MB, 0)
    # Oldest entries dropped; window spans at most ~300s.
    assert s.window
    span = s.window[-1][0] - s.window[0][0]
    assert span <= 300


def test_interface_name_retained():
    assert ThroughputSampler("Ethernet").interface == "Ethernet"


# ---------------------------------------------------------------------------
# Utilization & load tiers (Phase 3)
# ---------------------------------------------------------------------------

def test_recent_utilization():
    s = ThroughputSampler("eth0")
    s.poll(1000.0, 0, 0)
    s.poll(1001.0, 1 * MB, 0)  # 8 Mbps
    # 8 of contracted 100 → 8%.
    assert round(s.recent_utilization(100.0), 2) == 8.0


def test_recent_utilization_none_without_samples():
    s = ThroughputSampler("eth0")
    assert s.recent_utilization(100.0) is None


def test_recent_utilization_guards_zero_capacity():
    s = ThroughputSampler("eth0")
    s.poll(1000.0, 0, 0)
    s.poll(1001.0, 1 * MB, 0)
    assert s.recent_utilization(0) is None


@pytest.mark.parametrize("util, expected", [
    (0.0, "idle"), (4.9, "idle"),
    (5.0, "light"), (24.9, "light"),
    (25.0, "loaded"), (200.0, "loaded"),
])
def test_load_tier(util, expected):
    assert load_tier(util, 5.0, 25.0) == expected


def test_load_tier_none_when_unmeasured():
    # Never imply idle when load wasn't measured.
    assert load_tier(None, 5.0, 25.0) is None
