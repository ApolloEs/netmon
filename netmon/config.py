"""YAML config loader. Returns a frozen Config object at import time."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List

import yaml

# Resolve config path: env var > repo root default
_DEFAULT_PATH = Path(__file__).parent.parent / "config.yaml"
CONFIG_PATH = Path(os.environ.get("NETMON_CONFIG", _DEFAULT_PATH))


@dataclass(frozen=True)
class SpeedTestConfig:
    interval_hours: float
    soft_threshold: float
    hard_threshold: float
    postpone_retry_minutes: int
    max_postpones: int
    cli_path: str = "speedtest"


@dataclass(frozen=True)
class ConnectivityConfig:
    ping_interval_seconds: int
    outage_threshold_failures: int
    ping_targets: List[str]


@dataclass(frozen=True)
class DatabaseConfig:
    url: str


@dataclass(frozen=True)
class DashboardConfig:
    host: str
    port: int


@dataclass(frozen=True)
class ReportConfig:
    """Optional identity lines for the ISP evidence report header."""
    customer_name: str = ""
    account_number: str = ""
    isp_name: str = ""
    plan_name: str = ""


@dataclass(frozen=True)
class LoggingConfig:
    level: str
    file: str
    max_bytes: int
    backup_count: int


@dataclass(frozen=True)
class Config:
    target_mbps: float
    speed_test: SpeedTestConfig
    connectivity: ConnectivityConfig
    database: DatabaseConfig
    dashboard: DashboardConfig
    logging: LoggingConfig
    report: ReportConfig


def _validate(conf: Config) -> None:
    """Reject logically invalid configs with a descriptive error."""
    errors = []
    if conf.target_mbps <= 0:
        errors.append("target_mbps must be > 0")
    st = conf.speed_test
    if not (0 < st.soft_threshold <= st.hard_threshold):
        errors.append(
            "speed_test thresholds must satisfy 0 < soft_threshold <= hard_threshold "
            f"(got soft={st.soft_threshold}, hard={st.hard_threshold})"
        )
    if st.interval_hours <= 0:
        errors.append("speed_test.interval_hours must be > 0")
    if st.postpone_retry_minutes <= 0:
        errors.append("speed_test.postpone_retry_minutes must be > 0")
    if st.max_postpones < 0:
        errors.append("speed_test.max_postpones must be >= 0")
    if conf.connectivity.ping_interval_seconds <= 0:
        errors.append("connectivity.ping_interval_seconds must be > 0")
    if conf.connectivity.outage_threshold_failures < 1:
        errors.append("connectivity.outage_threshold_failures must be >= 1")
    if not conf.connectivity.ping_targets:
        errors.append("connectivity.ping_targets must not be empty")
    if not (1 <= conf.dashboard.port <= 65535):
        errors.append(f"dashboard.port must be 1-65535 (got {conf.dashboard.port})")
    if errors:
        raise ValueError("Invalid config:\n  - " + "\n  - ".join(errors))


def _from_raw(raw: dict) -> Config:
    """Build and validate a Config from a parsed YAML dict."""
    st = raw["speed_test"]
    conn = raw["connectivity"]

    try:
        lg = raw.get("logging", {})
        rp = raw.get("report") or {}
        conf = Config(
            target_mbps=raw["target_mbps"],
            speed_test=SpeedTestConfig(
                interval_hours=st["interval_hours"],
                soft_threshold=st["soft_threshold"],
                hard_threshold=st["hard_threshold"],
                postpone_retry_minutes=st["postpone_retry_minutes"],
                max_postpones=st["max_postpones"],
                cli_path=st.get("cli_path", "speedtest"),
            ),
            connectivity=ConnectivityConfig(
                ping_interval_seconds=conn["ping_interval_seconds"],
                outage_threshold_failures=conn["outage_threshold_failures"],
                ping_targets=conn["ping_targets"],
            ),
            database=DatabaseConfig(url=raw["database"]["url"]),
            dashboard=DashboardConfig(
                host=raw["dashboard"]["host"],
                port=raw["dashboard"]["port"],
            ),
            logging=LoggingConfig(
                level=lg.get("level", "INFO"),
                file=lg.get("file", "netmon.log"),
                max_bytes=lg.get("max_bytes", 10_485_760),
                backup_count=lg.get("backup_count", 5),
            ),
            report=ReportConfig(
                customer_name=rp.get("customer_name", ""),
                account_number=rp.get("account_number", ""),
                isp_name=rp.get("isp_name", ""),
                plan_name=rp.get("plan_name", ""),
            ),
        )
    except KeyError as exc:
        raise KeyError(f"Missing required config key: {exc}") from exc

    _validate(conf)
    return conf


def load(path: Path = CONFIG_PATH) -> Config:
    with open(path) as f:
        raw = yaml.safe_load(f)
    return _from_raw(raw)


# Keys editable from the dashboard settings modal. Everything else
# (database, dashboard, logging, cli_path) stays file-only.
_EDITABLE_SECTIONS = {
    "speed_test": {
        "interval_hours", "soft_threshold", "hard_threshold",
        "postpone_retry_minutes", "max_postpones",
    },
    "connectivity": {
        "ping_interval_seconds", "outage_threshold_failures", "ping_targets",
    },
}


def save_settings(updates: dict, path: Path = CONFIG_PATH) -> Config:
    """
    Apply whitelisted settings to config.yaml, preserving comments.
    Validates the resulting config before writing; raises ValueError
    (or KeyError) without touching the file if it is invalid.
    Returns the new Config.
    """
    from ruamel.yaml import YAML  # only the settings endpoint needs it

    yaml_rt = YAML()
    yaml_rt.preserve_quotes = True
    with open(path) as f:
        doc = yaml_rt.load(f)

    # Only assign keys whose value actually changed: replacing a node in the
    # ruamel tree can drop comments attached to it (notably list items), so
    # untouched keys must stay untouched.
    if "target_mbps" in updates and doc["target_mbps"] != updates["target_mbps"]:
        doc["target_mbps"] = updates["target_mbps"]
    for section, allowed in _EDITABLE_SECTIONS.items():
        for key, value in (updates.get(section) or {}).items():
            if key not in allowed:
                continue
            current = doc[section].get(key)
            if isinstance(value, list) and current is not None:
                changed = list(current) != list(value)
            else:
                changed = current != value
            if changed:
                doc[section][key] = value

    conf = _from_raw(doc)  # raises on invalid values

    with open(path, "w") as f:
        yaml_rt.dump(doc, f)
    return conf
