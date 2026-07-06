"""
One-time migration: add download_bytes / upload_bytes to speed_tests.

Schema-only change — no data rows are touched. Safe to re-run
(ADD COLUMN IF NOT EXISTS).

Usage:
    python scripts/add_bytes_columns.py
    python scripts/add_bytes_columns.py --config path/to/config.yaml
"""

import argparse
import sys
from pathlib import Path

# Allow running from the repo root without installing the package.
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text

from netmon import config as cfg
from netmon import db


def main() -> None:
    parser = argparse.ArgumentParser(description="Add bytes columns to speed_tests.")
    parser.add_argument("--config", type=Path, default=None, help="Path to config.yaml")
    args = parser.parse_args()

    conf = cfg.load(args.config) if args.config else cfg.load()
    engine = db.make_engine(conf.database.url)

    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE speed_tests ADD COLUMN IF NOT EXISTS download_bytes BIGINT"
        ))
        conn.execute(text(
            "ALTER TABLE speed_tests ADD COLUMN IF NOT EXISTS upload_bytes BIGINT"
        ))
    print("Columns download_bytes / upload_bytes present on speed_tests.")


if __name__ == "__main__":
    main()
