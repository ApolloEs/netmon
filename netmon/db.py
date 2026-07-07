"""SQLAlchemy Core table definitions and engine factory."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    create_engine,
)
from sqlalchemy.engine import Engine

# Shorthand: timezone-aware timestamp, maps to TZ on Postgres.
TZ = DateTime(timezone=True)

# Private/LAN addresses (the gateway target) — excluded from internet
# quality stats (latency, degraded detection): LAN behavior isn't ISP
# evidence. For use with Postgres `~` / `!~` operators.
PRIVATE_IP_SQL = r"^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.)"

metadata = MetaData()

speed_tests = Table(
    "speed_tests",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("timestamp", TZ, nullable=False),
    Column("download_mbps", Float),
    Column("upload_mbps", Float),
    Column("ping_ms", Float),
    Column("jitter_ms", Float),
    Column("packet_loss_pct", Float),
    Column("target_mbps", Float, nullable=False),
    Column("pct_of_target", Float),
    Column("server_id", Text),
    Column("server_name", Text),
    # Actual bytes transferred, as reported by the Ookla CLI. Nullable:
    # rows recorded before these columns existed have no byte data.
    Column("download_bytes", BigInteger),
    Column("upload_bytes", BigInteger),
)

test_events = Table(
    "test_events",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("timestamp", TZ, nullable=False),
    Column("status", Text, nullable=False),       # completed | postponed | skipped | forced
    Column("scheduled_for", TZ),
    Column("current_throughput_mbps", Float),
    Column("reason", Text),
    Column("retry_count", Integer, default=0),
    Column("speed_test_id", Integer, ForeignKey("speed_tests.id")),
)

outages = Table(
    "outages",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("started_at", TZ, nullable=False),
    Column("ended_at", TZ),
    Column("duration_seconds", Integer),
    Column("trigger", Text),
)

degraded_periods = Table(
    "degraded_periods",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("started_at", TZ, nullable=False),
    Column("ended_at", TZ),            # NULL while the period is ongoing
    Column("duration_seconds", Integer),
    Column("avg_loss_pct", Float),     # running average across windows
    Column("peak_loss_pct", Float),    # worst single window
    Column("windows_count", Integer),  # number of contributing windows
)

connectivity_pings = Table(
    "connectivity_pings",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("timestamp", TZ, nullable=False),
    Column("target", Text, nullable=False),
    Column("success", Boolean, nullable=False),
    Column("latency_ms", Float),
)

# Index defined separately so it mirrors the raw SQL schema exactly.
Index("idx_pings_timestamp", connectivity_pings.c.timestamp)
# Serves the status query's DISTINCT ON (target) ... ORDER BY target,
# timestamp DESC without a full sort (runs every ping cycle).
Index(
    "idx_pings_target_time",
    connectivity_pings.c.target,
    connectivity_pings.c.timestamp.desc(),
)


def make_engine(db_url: str) -> Engine:
    return create_engine(
        db_url,
        future=True,
        pool_size=3,
        max_overflow=2,
        pool_recycle=1800,  # recycle connections after 30 min to avoid stale connections
        pool_pre_ping=True, # test connection health before handing it to a job
    )


def create_tables(engine: Engine) -> None:
    """Create all tables if they don't exist."""
    metadata.create_all(engine)
