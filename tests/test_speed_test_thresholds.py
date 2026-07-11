"""
Unit tests for the speed-test decision logic: _check_thresholds and the
postpone/skip/force branches of the run() cycle. DB writes, bandwidth
sampling, and the Ookla CLI are all monkeypatched — no live resources.
"""

from types import SimpleNamespace

import pytest

from netmon import speed_test
from netmon.config import SpeedTestConfig


def st_conf(**overrides):
    base = dict(
        interval_hours=3,
        soft_threshold=0.5,
        hard_threshold=0.85,
        postpone_retry_minutes=15,
        max_postpones=3,
        cli_path="speedtest",
    )
    base.update(overrides)
    return SpeedTestConfig(**base)


# ---------------------------------------------------------------------------
# _check_thresholds decision matrix (target 100 Mbps)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    (0.0, 0.0),
    (94.3, 94.3),
    (10_000.0, 10_000.0),           # 10 Gbps — plausible, kept
    (None, None),
    (-1.0, None),                   # negative is impossible
    (-73786976294838.2, None),      # the observed INT64_MIN-derived value
    (1e15, None),                   # absurdly large
])
def test_sane_mbps(value, expected):
    assert speed_test._sane_mbps(value) == expected


@pytest.mark.parametrize("dl_mbps, expected", [
    (0.0, "proceed"),
    (49.9, "proceed"),
    (50.0, "proceed"),   # ratio == soft threshold: strict >, so proceed
    (50.1, "postpone"),
    (84.9, "postpone"),
    (85.0, "postpone"),  # ratio == hard threshold: strict >, so postpone
    (85.1, "skip"),
    (200.0, "skip"),
])
def test_check_thresholds(dl_mbps, expected):
    assert speed_test._check_thresholds(dl_mbps, 100.0, st_conf()) == expected


# ---------------------------------------------------------------------------
# run() cycle branches
# ---------------------------------------------------------------------------

OOKLA_JSON = {
    "download": {"bandwidth": 12_500_000, "bytes": 300_000_000},  # 100 Mbps
    "upload": {"bandwidth": 1_250_000, "bytes": 30_000_000},
    "ping": {"latency": 12.0, "jitter": 1.5},
    "packetLoss": 0.0,
    "server": {"id": 1234, "name": "Test Server"},
}


@pytest.fixture
def cycle(monkeypatch):
    """Patch out all side effects; returns a helper to run one cycle."""
    events = []
    monkeypatch.setattr(
        speed_test, "_write_event",
        lambda engine, status, **kw: events.append((status, kw)),
    )
    monkeypatch.setattr(speed_test, "_write_result", lambda engine, data, target, load: 42)
    monkeypatch.setattr(speed_test, "_run_speedtest_with_retry", lambda cli: OOKLA_JSON)

    def run(dl_now, force=False, retry_count=0, conf_over=None, cli_raises=False, sampler=None):
        monkeypatch.setattr(speed_test, "sample_bandwidth", lambda interval_seconds: (dl_now, 0.0))
        if cli_raises:
            def boom(cli):
                raise RuntimeError("CLI exploded")
            monkeypatch.setattr(speed_test, "_run_speedtest_with_retry", boom)
        conf = SimpleNamespace(
            target_mbps=100.0, speed_test=st_conf(**(conf_over or {})),
            monitoring=SimpleNamespace(idle_ceiling_pct=5.0, light_ceiling_pct=25.0),
        )
        return speed_test.run(
            engine=None, conf=conf, force=force, retry_count=retry_count, sampler=sampler,
        )

    run.events = events
    return run


class FakeSampler:
    """A sampler with a fixed utilization, for exercising the measured path."""
    def __init__(self, util_pct):
        self._util = util_pct
        self._down = (util_pct or 0) / 100 * 100.0  # Mbps at 100 Mbps capacity

    def latest_down_mbps(self):
        return self._down

    def latest_up_mbps(self):
        return 1.0

    def recent_utilization(self, contracted_down):
        return self._util


# --- Legacy fallback path (no sampler): download-vs-threshold ------------

def test_idle_line_completes(cycle):
    assert cycle(dl_now=5.0) == ("completed", 42)
    assert cycle.events[-1][0] == "completed"
    assert cycle.events[-1][1]["speed_test_id"] == 42


def test_soft_threshold_postpones(cycle):
    assert cycle(dl_now=60.0) == ("postponed", None)
    assert cycle.events[-1][0] == "postponed"


def test_hard_threshold_skips(cycle):
    assert cycle(dl_now=90.0) == ("skipped", None)
    assert cycle.events[-1][0] == "skipped"


def test_max_postpones_forces_run(cycle):
    # Busy line, but the postpone budget is exhausted → force-run so the
    # schedule never develops blind spots.
    assert cycle(dl_now=60.0, retry_count=3) == ("forced", 42)
    assert cycle.events[-1][0] == "forced"


def test_postpone_below_budget_still_postpones(cycle):
    assert cycle(dl_now=60.0, retry_count=2) == ("postponed", None)


def test_zero_max_postpones_never_postpones(cycle):
    assert cycle(dl_now=60.0, conf_over={"max_postpones": 0}) == ("forced", 42)


def test_manual_force_bypasses_hard_threshold(cycle):
    assert cycle(dl_now=95.0, force=True) == ("forced", 42)
    assert cycle.events[-1][1]["reason"] == "manual run from dashboard"


def test_cli_failure_records_error_event(cycle):
    assert cycle(dl_now=5.0, cli_raises=True) == ("error", None)
    assert cycle.events[-1][0] == "error"
    assert "CLI exploded" in cycle.events[-1][1]["reason"]


def test_concurrent_run_reports_busy(cycle):
    with speed_test._run_lock:
        assert cycle(dl_now=5.0) == ("busy", None)
    assert speed_test.is_running() is False


# --- Measured path (sampler present): utilization-based ------------------

def test_measured_idle_completes(cycle):
    # 2% of capacity = idle → run normally.
    assert cycle(dl_now=0, sampler=FakeSampler(2.0)) == ("completed", 42)
    assert cycle.events[-1][0] == "completed"


def test_measured_light_completes(cycle):
    # 15% = light → still usable, runs (caveated by the stored load_tier).
    assert cycle(dl_now=0, sampler=FakeSampler(15.0)) == ("completed", 42)


def test_measured_loaded_postpones(cycle):
    # 40% = loaded, budget remains → postpone with a utilization reason.
    assert cycle(dl_now=0, sampler=FakeSampler(40.0)) == ("postponed", None)
    assert cycle.events[-1][0] == "postponed"
    assert "of capacity (loaded)" in cycle.events[-1][1]["reason"]


def test_measured_loaded_never_skips_only_postpones(cycle):
    # Even very high local use postpones (retries remain) — never a silent skip.
    assert cycle(dl_now=0, sampler=FakeSampler(300.0)) == ("postponed", None)


def test_measured_loaded_exhausted_forces_and_labels(cycle):
    # Loaded but out of postpones → force-run, labelled as compromised.
    assert cycle(dl_now=0, retry_count=3, sampler=FakeSampler(40.0)) == ("forced", 42)
    ev = cycle.events[-1]
    assert ev[0] == "forced"
    assert "under load" in ev[1]["reason"] and "loaded" in ev[1]["reason"]


def test_measured_empty_window_falls_back_to_legacy(cycle):
    # Sampler present but no reading yet (util None) → legacy threshold path.
    assert cycle(dl_now=90.0, sampler=FakeSampler(None)) == ("skipped", None)
