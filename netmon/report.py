"""
ISP evidence report — renders a fully self-contained bilingual HTML page
(styles, data, and Chart.js all inlined) that prints cleanly to PDF.

Public API:
    render_report(engine, conf, days) -> str (HTML)
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.engine import Engine

from netmon import config as cfg
from netmon import queries
from netmon.utils import now

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_VENDOR = Path(__file__).parent / "static" / "vendor"
# Chart.js plus the date-fns adapter (the time axis needs it).
_CHART_FILES = ["chart.umd.min.js", "chartjs-adapter-date-fns.bundle.min.js"]


def render_report(engine: Engine, conf: cfg.Config, days: int = 30) -> str:
    stats = queries.get_report_stats(engine, days)
    generated_at = now()

    meta = {
        "days": days,
        "generated_at": generated_at.isoformat(),
        "period_start": (generated_at - timedelta(days=days)).isoformat(),
        "target_mbps": conf.target_mbps,
        "ping_interval_seconds": conf.connectivity.ping_interval_seconds,
        "outage_threshold_failures": conf.connectivity.outage_threshold_failures,
        "interval_hours": conf.speed_test.interval_hours,
        "degraded_loss_threshold_pct": conf.connectivity.degraded_loss_threshold_pct,
        "degraded_window_minutes": conf.connectivity.degraded_window_minutes,
        "idle_ceiling_pct": conf.monitoring.idle_ceiling_pct,
        "report": {
            "customer_name": conf.report.customer_name,
            "account_number": conf.report.account_number,
            "isp_name": conf.report.isp_name,
            "plan_name": conf.report.plan_name,
        },
    }

    env = Environment(
        loader=FileSystemLoader(_TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("report.html")
    return template.render(
        meta=meta,
        stats_json=json.dumps(stats, ensure_ascii=False),
        meta_json=json.dumps(meta, ensure_ascii=False),
        chart_js="\n;\n".join(
            (_VENDOR / f).read_text(encoding="utf-8") for f in _CHART_FILES
        ),
    )
