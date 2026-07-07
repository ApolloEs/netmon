"""
Quick smoke-test for the pinger. Runs one ping cycle and prints the
rows written to connectivity_pings.

Usage:
    python scripts/test_pinger.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

from sqlalchemy import select, func
from netmon import config as cfg
from netmon import db
from netmon.pinger import PingerState, resolve_targets, run_once
from netmon.db import connectivity_pings

conf = cfg.load()
engine = db.make_engine(conf.database.url)

targets = resolve_targets(conf.connectivity.ping_targets)
print(f"\nResolved targets: {targets}\n")

# Count rows before
with engine.connect() as conn:
    before = conn.execute(select(func.count()).select_from(connectivity_pings)).scalar()

run_once(engine, conf, targets, PingerState())

# Show new rows
with engine.connect() as conn:
    after = conn.execute(select(func.count()).select_from(connectivity_pings)).scalar()
    rows = conn.execute(
        select(connectivity_pings)
        .order_by(connectivity_pings.c.id.desc())
        .limit(after - before)
    ).fetchall()

print(f"\nRows written: {after - before}")
for row in reversed(rows):
    status = f"{row.latency_ms:.1f}ms" if row.latency_ms else "FAIL"
    print(f"  {row.timestamp}  {row.target:<15}  {status}")
