"""
Generate the ISP evidence report as a self-contained HTML file.
Open it in a browser and print to PDF (Ctrl+P).

Usage:
    python scripts/generate_report.py
    python scripts/generate_report.py --days 90 --out my-report.html
"""

import argparse
import sys
from datetime import date
from pathlib import Path

# Allow running from the repo root without installing the package.
sys.path.insert(0, str(Path(__file__).parent.parent))

from netmon import config as cfg
from netmon import db, report


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the NetMon ISP evidence report.")
    parser.add_argument("--days", type=int, default=30, help="Reporting period in days (default 30)")
    parser.add_argument("--out", type=Path, default=None, help="Output file path")
    parser.add_argument("--config", type=Path, default=None, help="Path to config.yaml")
    args = parser.parse_args()

    conf = cfg.load(args.config) if args.config else cfg.load()
    engine = db.make_engine(conf.database.url)

    out = args.out or Path(f"netmon-report-{date.today().isoformat()}.html")
    html = report.render_report(engine, conf, days=args.days)
    out.write_text(html, encoding="utf-8")
    print(f"Report written: {out.resolve()}  ({len(html) // 1024} KB, {args.days} days)")


if __name__ == "__main__":
    main()
