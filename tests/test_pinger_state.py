"""
Unit tests for PingerState transitions and _handle_result's use of them.
DB-write helpers are monkeypatched — no live database.
"""

from netmon import pinger
from netmon.pinger import PingerState

THRESHOLD = 3


def test_streak_increments_per_failure():
    s = PingerState()
    assert s.record_failure("1.1.1.1", THRESHOLD) == (1, False)
    assert s.record_failure("1.1.1.1", THRESHOLD) == (2, False)


def test_outage_opens_exactly_at_threshold():
    s = PingerState()
    s.record_failure("1.1.1.1", THRESHOLD)
    s.record_failure("1.1.1.1", THRESHOLD)
    streak, should_open = s.record_failure("1.1.1.1", THRESHOLD)
    assert (streak, should_open) == (3, True)


def test_outage_does_not_reopen_while_active():
    s = PingerState()
    for _ in range(THRESHOLD):
        s.record_failure("1.1.1.1", THRESHOLD)
    s.outage_opened("1.1.1.1", 7)
    streak, should_open = s.record_failure("1.1.1.1", THRESHOLD)
    assert streak == 4
    assert should_open is False


def test_success_resets_streak_and_reports_previous():
    s = PingerState()
    s.record_failure("1.1.1.1", THRESHOLD)
    s.record_failure("1.1.1.1", THRESHOLD)
    prev, to_close = s.record_success("1.1.1.1")
    assert prev == 2
    assert to_close is None  # threshold never reached, nothing to close
    assert s.record_failure("1.1.1.1", THRESHOLD) == (1, False)


def test_success_closes_open_outage():
    s = PingerState()
    for _ in range(THRESHOLD):
        s.record_failure("1.1.1.1", THRESHOLD)
    s.outage_opened("1.1.1.1", 7)
    prev, to_close = s.record_success("1.1.1.1")
    assert (prev, to_close) == (3, 7)
    # Closed: a second success has nothing more to close.
    assert s.record_success("1.1.1.1") == (0, None)


def test_targets_are_independent():
    s = PingerState()
    for _ in range(THRESHOLD):
        s.record_failure("1.1.1.1", THRESHOLD)
    assert s.record_failure("8.8.8.8", THRESHOLD) == (1, False)


# ---------------------------------------------------------------------------
# _handle_result drives the DB helpers correctly
# ---------------------------------------------------------------------------

def _patch_db(monkeypatch, calls):
    monkeypatch.setattr(
        pinger, "_record_ping",
        lambda engine, target, success, latency: calls.append(("ping", target, success)),
    )
    monkeypatch.setattr(
        pinger, "_open_outage_record",
        lambda engine, target: calls.append(("open", target)) or 99,
    )
    monkeypatch.setattr(
        pinger, "_close_outage_record",
        lambda engine, outage_id: calls.append(("close", outage_id)),
    )


def test_handle_result_full_outage_lifecycle(monkeypatch):
    calls = []
    _patch_db(monkeypatch, calls)
    s = PingerState()

    for _ in range(THRESHOLD):
        pinger._handle_result(None, s, "1.1.1.1", False, None, THRESHOLD)
    assert ("open", "1.1.1.1") in calls
    assert s.open_outage["1.1.1.1"] == 99

    pinger._handle_result(None, s, "1.1.1.1", True, 20.0, THRESHOLD)
    assert ("close", 99) in calls
    assert s.open_outage["1.1.1.1"] is None
    assert s.fail_streak["1.1.1.1"] == 0


def test_handle_result_below_threshold_never_opens(monkeypatch):
    calls = []
    _patch_db(monkeypatch, calls)
    s = PingerState()

    pinger._handle_result(None, s, "1.1.1.1", False, None, THRESHOLD)
    pinger._handle_result(None, s, "1.1.1.1", True, 20.0, THRESHOLD)
    assert not [c for c in calls if c[0] in ("open", "close")]
    # Every result is recorded regardless.
    assert len([c for c in calls if c[0] == "ping"]) == 2
