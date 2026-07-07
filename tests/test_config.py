"""Unit tests for config loading, validation, and comment-preserving saves."""

import copy
import shutil
from pathlib import Path

import pytest
import yaml

from netmon import config as cfg

EXAMPLE = Path(__file__).parent.parent / "config.example.yaml"


@pytest.fixture
def raw():
    with open(EXAMPLE) as f:
        return yaml.safe_load(f)


def test_example_config_is_valid(raw):
    conf = cfg._from_raw(raw)
    assert conf.target_mbps == 100
    assert conf.speed_test.soft_threshold == 0.5
    assert conf.connectivity.ping_targets[:2] == ["1.1.1.1", "8.8.8.8"]
    assert conf.dashboard.port == 5000


def test_load_reads_example_file():
    conf = cfg.load(EXAMPLE)
    assert conf.speed_test.interval_hours == 3


def test_optional_sections_get_defaults(raw):
    del raw["logging"]
    conf = cfg._from_raw(raw)
    assert conf.logging.level == "INFO"
    assert conf.report.customer_name == ""


@pytest.mark.parametrize("mutate, match", [
    (lambda r: r.update(target_mbps=0), "target_mbps"),
    (lambda r: r.update(target_mbps=-5), "target_mbps"),
    (lambda r: r["speed_test"].update(soft_threshold=0.9, hard_threshold=0.5), "thresholds"),
    (lambda r: r["speed_test"].update(soft_threshold=0), "thresholds"),
    (lambda r: r["speed_test"].update(interval_hours=0), "interval_hours"),
    (lambda r: r["speed_test"].update(postpone_retry_minutes=0), "postpone_retry_minutes"),
    (lambda r: r["speed_test"].update(max_postpones=-1), "max_postpones"),
    (lambda r: r["connectivity"].update(ping_interval_seconds=0), "ping_interval_seconds"),
    (lambda r: r["connectivity"].update(outage_threshold_failures=0), "outage_threshold"),
    (lambda r: r["connectivity"].update(ping_targets=[]), "ping_targets"),
    (lambda r: r["connectivity"].update(degraded_loss_threshold_pct=0), "degraded_loss"),
    (lambda r: r["connectivity"].update(degraded_loss_threshold_pct=100), "degraded_loss"),
    (lambda r: r["connectivity"].update(degraded_window_minutes=0), "degraded_window"),
    (lambda r: r["dashboard"].update(port=0), "port"),
    (lambda r: r["dashboard"].update(port=70000), "port"),
])
def test_invalid_values_rejected(raw, mutate, match):
    bad = copy.deepcopy(raw)
    mutate(bad)
    with pytest.raises(ValueError, match=match):
        cfg._from_raw(bad)


@pytest.mark.parametrize("key", ["target_mbps", "speed_test", "connectivity", "database"])
def test_missing_required_key_rejected(raw, key):
    bad = copy.deepcopy(raw)
    del bad[key]
    with pytest.raises(KeyError):
        cfg._from_raw(bad)


def test_missing_nested_key_rejected(raw):
    bad = copy.deepcopy(raw)
    del bad["speed_test"]["interval_hours"]
    with pytest.raises(KeyError, match="interval_hours"):
        cfg._from_raw(bad)


# ---------------------------------------------------------------------------
# save_settings: round-trip + comment preservation (ruamel)
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_config(tmp_path):
    dest = tmp_path / "config.yaml"
    shutil.copy(EXAMPLE, dest)
    return dest


def test_save_settings_round_trips_values(tmp_config):
    conf = cfg.save_settings(
        {
            "target_mbps": 200,
            "speed_test": {"interval_hours": 6},
            "connectivity": {"ping_interval_seconds": 30},
        },
        path=tmp_config,
    )
    assert conf.target_mbps == 200
    assert conf.speed_test.interval_hours == 6

    reloaded = cfg.load(tmp_config)
    assert reloaded.target_mbps == 200
    assert reloaded.speed_test.interval_hours == 6
    assert reloaded.connectivity.ping_interval_seconds == 30
    # Untouched values survive.
    assert reloaded.speed_test.soft_threshold == 0.5


def test_save_settings_preserves_comments(tmp_config):
    cfg.save_settings({"speed_test": {"interval_hours": 6}}, path=tmp_config)
    text = tmp_config.read_text()
    assert "# postpone if current use > 50% of target" in text
    assert "# resolved at runtime by the pinger" in text


def test_save_settings_ignores_non_whitelisted_keys(tmp_config):
    cfg.save_settings({"speed_test": {"cli_path": "evil.exe"}}, path=tmp_config)
    assert cfg.load(tmp_config).speed_test.cli_path == "speedtest"


def test_save_settings_rejects_invalid_without_writing(tmp_config):
    before = tmp_config.read_text()
    with pytest.raises(ValueError):
        cfg.save_settings({"target_mbps": -1}, path=tmp_config)
    assert tmp_config.read_text() == before
