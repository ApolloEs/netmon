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
    # Local host load context at measurement time (NULL when host-throughput
    # monitoring was disabled or the interface wasn't detected — never assume
    # idle from absence). See netmon/throughput.py.
    Column("local_down_mbps", Float),
    Column("local_up_mbps", Float),
    Column("utilization_pct", Float),
    Column("load_tier", Text),
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

# The two interval tables below also carry local host load context (mean
# over the interval): local_down_mbps, local_up_mbps, utilization_pct,
# load_tier — populated by queries.annotate_interval_loads(); NULL until
# then and for intervals with no throughput samples.

outages = Table(
    "outages",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("started_at", TZ, nullable=False),
    Column("ended_at", TZ),
    Column("duration_seconds", Integer),
    Column("trigger", Text),
    Column("local_down_mbps", Float),
    Column("local_up_mbps", Float),
    Column("utilization_pct", Float),
    Column("load_tier", Text),
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
    Column("local_down_mbps", Float),
    Column("local_up_mbps", Float),
    Column("utilization_pct", Float),
    Column("load_tier", Text),
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

host_throughput = Table(
    "host_throughput",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("timestamp", TZ, nullable=False),
    Column("interface", Text, nullable=False),
    Column("down_mbps", Float),
    Column("up_mbps", Float),
)

# Index defined separately so it mirrors the raw SQL schema exactly.
Index("idx_pings_timestamp", connectivity_pings.c.timestamp)
# Serves retention pruning and interval-mean lookups over host_throughput.
Index("idx_throughput_timestamp", host_throughput.c.timestamp)
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


# Idempotent DDL for schema elements added after a table already existed —
# metadata.create_all() only creates missing tables, never missing columns
# or indexes. Every statement is IF-NOT-EXISTS; schema-only, no data rows.
MIGRATIONS = [
    # Real bytes transferred per speed test (Ookla JSON), for data-cost stats.
    "ALTER TABLE speed_tests ADD COLUMN IF NOT EXISTS download_bytes BIGINT",
    "ALTER TABLE speed_tests ADD COLUMN IF NOT EXISTS upload_bytes BIGINT",
    # Composite index for the dashboard status query (DISTINCT ON target).
    "CREATE INDEX IF NOT EXISTS idx_pings_target_time "
    "ON connectivity_pings (target, timestamp DESC)",
    # Local host load context (Phase 4) — additive, existing rows stay NULL.
    *[
        f"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS {col} {typ}"
        for tbl in ("speed_tests", "outages", "degraded_periods")
        for col, typ in (
            ("local_down_mbps", "DOUBLE PRECISION"),
            ("local_up_mbps", "DOUBLE PRECISION"),
            ("utilization_pct", "DOUBLE PRECISION"),
            ("load_tier", "TEXT"),
        )
    ],
]

# Arbitrary but fixed app-wide key for the schema advisory lock ("NETM").
_SCHEMA_LOCK_KEY = 0x4E45544D


def ensure_schema(engine: Engine) -> None:
    """
    Create missing tables and apply the idempotent migrations. Runs at
    every startup so a fresh database (e.g. first `docker compose up`)
    needs no manual init step. A transaction-scoped advisory lock keeps
    concurrent starts from racing on DDL.
    """
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": _SCHEMA_LOCK_KEY}
        )
        metadata.create_all(conn)
        for stmt in MIGRATIONS:
            conn.execute(text(stmt))
