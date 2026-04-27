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


def load(path: Path = CONFIG_PATH) -> Config:
    with open(path) as f:
        raw = yaml.safe_load(f)

    st = raw["speed_test"]
    conn = raw["connectivity"]

    try:
        lg = raw.get("logging", {})
        return Config(
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
        )
    except KeyError as exc:
        raise KeyError(f"Missing required config key: {exc}") from exc
