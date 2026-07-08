"""
Idempotent schema migrations for an existing NetMon database.

metadata.create_all() only creates missing *tables*, so columns and
indexes added after a table exists need explicit DDL. Every statement
here is IF-NOT-EXISTS — safe to re-run any time. Schema-only; no data
rows are touched. Supersedes scripts/add_bytes_columns.py.

Usage:
    python scripts/migrate.py
    python scripts/migrate.py --config path/to/config.yaml
"""

import argparse
import sys
from pathlib import Path

# Allow running from the repo root without installing the package.
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text

from netmon import config as cfg
from netmon import db

# The statements live in netmon.db (shared with the automatic startup
# ensure_schema()); this script remains for explicit, visible runs.
MIGRATIONS = db.MIGRATIONS


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply idempotent NetMon migrations.")
    parser.add_argument("--config", type=Path, default=None, help="Path to config.yaml")
    args = parser.parse_args()

    conf = cfg.load(args.config) if args.config else cfg.load()
    engine = db.make_engine(conf.database.url)

    with engine.begin() as conn:
        for stmt in MIGRATIONS:
            conn.execute(text(stmt))
            print(f"OK: {stmt.split(' ON ')[0][:70]}")
    print("All migrations applied.")


if __name__ == "__main__":
    main()
