"""
Smoke-test for the speed test runner. Runs one full cycle and prints
the rows written to speed_tests and test_events.

Usage:
    python scripts/test_speed_test.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

from sqlalchemy import select, func
from netmon import config as cfg
from netmon import db
from netmon.db import speed_tests, test_events
from netmon import speed_test

conf = cfg.load()
engine = db.make_engine(conf.database.url)

with engine.connect() as conn:
    before_st = conn.execute(select(func.count()).select_from(speed_tests)).scalar()
    before_ev = conn.execute(select(func.count()).select_from(test_events)).scalar()

speed_test.run(engine, conf)

with engine.connect() as conn:
    rows_st = conn.execute(
        select(speed_tests).order_by(speed_tests.c.id.desc()).limit(1)
    ).fetchall()
    new_events = conn.execute(
        select(test_events)
        .order_by(test_events.c.id.desc())
        .limit(conn.execute(select(func.count()).select_from(test_events)).scalar() - before_ev)
    ).fetchall()

print("\n--- speed_tests ---")
for row in rows_st:
    print(f"  id={row.id}  {row.download_mbps:.1f} Mbps down  "
          f"{row.upload_mbps:.1f} Mbps up  {row.pct_of_target:.0f}% of target  "
          f"ping={row.ping_ms:.0f}ms  server={row.server_name}")

print("\n--- test_events ---")
for row in reversed(new_events):
    print(f"  id={row.id}  status={row.status}  "
          f"throughput={row.current_throughput_mbps:.2f} Mbps  "
          f"retries={row.retry_count}  speed_test_id={row.speed_test_id}")
