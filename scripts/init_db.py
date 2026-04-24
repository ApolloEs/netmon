"""
Run once to create all tables and verify the database connection.

Usage:
    python scripts/init_db.py
    python scripts/init_db.py --config path/to/config.yaml
"""

import argparse
import sys
from pathlib import Path

# Allow running from the repo root without installing the package.
sys.path.insert(0, str(Path(__file__).parent.parent))

from netmon import config as cfg
from netmon import db


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialise the NetMon database.")
    parser.add_argument("--config", type=Path, default=None, help="Path to config.yaml")
    args = parser.parse_args()

    conf = cfg.load(args.config) if args.config else cfg.load()

    print(f"Connecting to: {conf.database.url}")
    engine = db.make_engine(conf.database.url)

    # Verify connectivity before touching schema.
    from sqlalchemy import text
    with engine.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1
    print("Connection OK.")

    db.create_tables(engine)
    print("Tables created (or already exist).")

    # Report what's there.
    from sqlalchemy import inspect
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    print(f"Tables in database: {', '.join(sorted(tables))}")


if __name__ == "__main__":
    main()
