"""
Run only the Flask dashboard (no pinger, no speed tests, no scheduler).

Read-only view of whatever is already in the database. Useful for browsing
recorded data without starting a full monitoring run. Live SSE updates will
not fire because nothing is publishing events; reload the page to refresh.

Usage:
    python scripts/run_dashboard.py
    python scripts/run_dashboard.py --config path/to/config.yaml
"""

import argparse
import sys
from pathlib import Path

# Allow running from the repo root without installing the package.
sys.path.insert(0, str(Path(__file__).parent.parent))

from netmon import config as cfg
from netmon import db
from netmon.dashboard import create_app
from netmon.runtime import Runtime


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the NetMon dashboard only.")
    parser.add_argument("--config", type=Path, default=None, help="Path to config.yaml")
    args = parser.parse_args()

    conf = cfg.load(args.config) if args.config else cfg.load()
    engine = db.make_engine(conf.database.url)

    from waitress import serve

    app = create_app(Runtime(engine, conf))  # scheduler stays None: read-only mode
    print(f"Dashboard (read-only) → http://{conf.dashboard.host}:{conf.dashboard.port}")
    serve(app, host=conf.dashboard.host, port=conf.dashboard.port, threads=8)


if __name__ == "__main__":
    main()
