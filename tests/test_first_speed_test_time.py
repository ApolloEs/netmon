"""
Unit tests for main._first_speed_test_time — the restart-safe schedule
clamp. Uses a fake engine so no database is involved.
"""

from datetime import timedelta

from netmon.main import _first_speed_test_time
from netmon.utils import now


class FakeConn:
    def __init__(self, last):
        self._last = last

    def execute(self, _stmt):
        return self

    def scalar(self):
        return self._last

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeEngine:
    def __init__(self, last):
        self._last = last

    def connect(self):
        return FakeConn(self._last)


def test_empty_table_runs_almost_immediately():
    before = now()
    result = _first_speed_test_time(FakeEngine(None), interval_hours=3)
    assert before + timedelta(seconds=14) <= result <= now() + timedelta(seconds=16)


def test_recent_test_schedules_last_plus_interval():
    last = now() - timedelta(hours=1)
    result = _first_speed_test_time(FakeEngine(last), interval_hours=3)
    expected = last + timedelta(hours=3)
    assert abs((result - expected).total_seconds()) < 2


def test_stale_test_clamps_to_earliest():
    # Last test long overdue → run right away (+15s), not in the past.
    last = now() - timedelta(hours=10)
    result = _first_speed_test_time(FakeEngine(last), interval_hours=3)
    assert result <= now() + timedelta(seconds=16)
    assert result > now()


def test_future_timestamp_clamps_to_one_interval():
    # A clock anomaly can't push the first run beyond now + interval.
    last = now() + timedelta(hours=5)
    result = _first_speed_test_time(FakeEngine(last), interval_hours=3)
    assert result <= now() + timedelta(hours=3, seconds=2)
