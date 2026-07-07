"""Unit tests for queries._group_outages — read-time outage grouping."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from netmon.queries import _group_outages

T0 = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def row(start_offset_s, end_offset_s, trigger, is_open=False):
    return SimpleNamespace(
        started_at=T0 + timedelta(seconds=start_offset_s),
        ended_at=T0 + timedelta(seconds=end_offset_s),
        trigger=trigger,
        is_open=is_open,
    )


def test_empty_input():
    assert _group_outages([]) == []


def test_single_anchor_becomes_connection_event():
    out = _group_outages([row(0, 90, "1.1.1.1")])
    assert len(out) == 1
    event = out[0]
    assert event["type"] == "connection"
    assert event["triggers"] == ["1.1.1.1"]
    assert event["duration_seconds"] == 90
    assert event["is_open"] is False


def test_overlapping_anchors_merge():
    out = _group_outages([
        row(0, 100, "1.1.1.1"),
        row(30, 120, "8.8.8.8"),
    ])
    assert len(out) == 1
    assert out[0]["triggers"] == ["1.1.1.1", "8.8.8.8"]
    assert out[0]["duration_seconds"] == 120


def test_anchors_within_merge_gap_merge():
    # Second outage starts exactly at end + 60s (the merge gap boundary).
    out = _group_outages([
        row(0, 60, "1.1.1.1"),
        row(120, 180, "8.8.8.8"),
    ])
    assert len(out) == 1


def test_anchors_beyond_merge_gap_stay_separate():
    out = _group_outages([
        row(0, 60, "1.1.1.1"),
        row(121, 180, "8.8.8.8"),
    ])
    assert len(out) == 2


def test_host_folds_into_containing_connection_event():
    out = _group_outages([
        row(0, 300, "1.1.1.1"),
        row(60, 240, "google.com"),
    ])
    assert len(out) == 1
    assert out[0]["type"] == "connection"
    assert "google.com" in out[0]["triggers"]


def test_host_folds_within_tolerance():
    # Host detected 100s before the anchor cluster starts — within the
    # 120s fold tolerance, so it still counts as the same event.
    out = _group_outages([
        row(100, 300, "1.1.1.1"),
        row(0, 250, "google.com"),
    ])
    assert len(out) == 1
    # Folding extends the event to cover the host interval.
    assert out[0]["started_at"] == T0.isoformat()


def test_standalone_host_event():
    out = _group_outages([row(0, 120, "google.com")])
    assert len(out) == 1
    assert out[0]["type"] == "host"
    assert out[0]["triggers"] == ["google.com"]


def test_host_outside_any_event_stands_alone():
    out = _group_outages([
        row(0, 60, "1.1.1.1"),
        row(600, 700, "google.com"),
    ])
    assert len(out) == 2
    types = {e["type"] for e in out}
    assert types == {"connection", "host"}


def test_open_outage_has_no_duration():
    out = _group_outages([row(0, 90, "1.1.1.1", is_open=True)])
    assert out[0]["is_open"] is True
    assert out[0]["duration_seconds"] is None


def test_open_flag_survives_merge():
    out = _group_outages([
        row(0, 100, "1.1.1.1"),
        row(30, 120, "8.8.8.8", is_open=True),
    ])
    assert len(out) == 1
    assert out[0]["is_open"] is True


def test_result_sorted_newest_first():
    out = _group_outages([
        row(0, 60, "1.1.1.1"),
        row(1000, 1060, "8.8.8.8"),
    ])
    assert out[0]["started_at"] > out[1]["started_at"]
