"""
LineProof entry point — wires all modules into a long-running scheduled process.

Usage:
    python -m netmon.main
    python -m netmon.main --config path/to/config.yaml
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from datetime import timedelta
from pathlib import Path

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from waitress import serve as waitress_serve

from netmon import cleanup, config as cfg, db, jobs, outage_detector, pinger
from netmon.dashboard import create_app
from netmon.runtime import Runtime
from netmon.utils import now, setup_logging

log = logging.getLogger(__name__)


# ── Scheduler setup ───────────────────────────────────────────────────

def _first_speed_test_time(engine, interval_hours: float):
    """
    First speed-test run: last recorded test + interval, clamped to
    [now + 15s, now + interval]. Prevents a restart loop from burning
    300-500 MB per cycle while behaving like a fresh install when the
    table is empty.
    """
    from sqlalchemy import func, select

    with engine.connect() as conn:
        last = conn.execute(select(func.max(db.speed_tests.c.timestamp))).scalar()

    earliest = now() + timedelta(seconds=15)
    latest = now() + timedelta(hours=interval_hours)
    if last is None:
        return earliest
    return min(max(last + timedelta(hours=interval_hours), earliest), latest)


def _build_scheduler() -> BlockingScheduler:
    return BlockingScheduler(
        executors={"default": ThreadPoolExecutor(max_workers=5)},
        job_defaults={
            "coalesce": True,
            "max_instances": 1,
            "misfire_grace_time": 60,
        },
    )


def _register_listeners(scheduler: BlockingScheduler) -> None:
    def on_event(event):
        if hasattr(event, "exception") and event.exception:
            log.error(
                "Job '%s' raised an unhandled exception: %s",
                event.job_id, event.exception, exc_info=event.traceback,
            )
        elif event.code == EVENT_JOB_MISSED:
            log.warning("Job '%s' missed its scheduled run time.", event.job_id)

    scheduler.add_listener(on_event, EVENT_JOB_ERROR | EVENT_JOB_MISSED)


def _register_signal_handlers(scheduler: BlockingScheduler) -> None:
    def handle_shutdown(signum, frame):
        name = signal.Signals(signum).name
        log.info("Received %s — stopping scheduler.", name)
        scheduler.shutdown(wait=True)
        log.info("LineProof stopped cleanly.")
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_shutdown)
    try:
        signal.signal(signal.SIGTERM, handle_shutdown)
    except (OSError, ValueError):
        pass


# ── Main ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="LineProof — internet connection monitor.")
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args()

    conf = cfg.load(args.config) if args.config else cfg.load()

    setup_logging(
        level=conf.logging.level,
        log_file=conf.logging.file,
        max_bytes=conf.logging.max_bytes,
        backup_count=conf.logging.backup_count,
    )

    log.info("LineProof starting up.")

    engine = db.make_engine(conf.database.url)
    rt = Runtime(engine, conf)

    log.info("Ensuring database schema (create-if-missing + idempotent migrations)...")
    try:
        db.ensure_schema(engine)
    except OperationalError as exc:
        # The most common first-run failure (Postgres down, wrong password,
        # URL typo) — one actionable line instead of a 30-line traceback.
        # Anything other than a connection-level error still raises normally.
        masked = make_url(conf.database.url).render_as_string(hide_password=True)
        reason = str(exc.orig).strip().splitlines()[0] if exc.orig else str(exc)
        log.error(
            "Cannot connect to the database at %s. Is PostgreSQL running, and do "
            "the credentials in config.yaml match? See the Quick start in "
            "README.md. (%s)",
            masked, reason,
        )
        sys.exit(1)

    log.info("Running startup outage reconcile...")
    outage_detector.reconcile(engine, conf)

    targets = pinger.resolve_targets(conf.connectivity.ping_targets)
    if not targets:
        log.error("No ping targets resolved — cannot start. Check config.")
        sys.exit(1)

    rt.pinger_state = pinger.restore_state(engine, targets)
    rt.targets = targets

    # ── Dashboard thread ─────────────────────────────────────────────
    # waitress instead of Flask's dev server: production-grade, works on
    # Windows and the Pi. 8 threads — each SSE client parks one.
    flask_app = create_app(rt)
    dash_thread = threading.Thread(
        target=lambda: waitress_serve(
            flask_app,
            host=conf.dashboard.host,
            port=conf.dashboard.port,
            threads=8,
        ),
        name="dashboard",
        daemon=True,
    )
    dash_thread.start()
    log.info("Dashboard → http://%s:%d", conf.dashboard.host, conf.dashboard.port)

    # ── Scheduler ────────────────────────────────────────────────────
    scheduler = _build_scheduler()
    _register_listeners(scheduler)
    _register_signal_handlers(scheduler)

    scheduler.add_job(
        lambda: jobs.pinger_job(rt),
        trigger="interval",
        seconds=conf.connectivity.ping_interval_seconds,
        id="pinger",
        name="Connectivity Pinger",
        next_run_time=now(),
    )

    first_test = _first_speed_test_time(engine, conf.speed_test.interval_hours)
    log.info("First speed test scheduled for %s.", first_test.isoformat())
    scheduler.add_job(
        lambda: jobs.speed_test_job(rt),
        trigger="interval",
        hours=conf.speed_test.interval_hours,
        id="speed_test",
        name="Speed Test",
        next_run_time=first_test,
    )

    scheduler.add_job(
        lambda: outage_detector.reconcile(rt.engine, rt.conf),
        trigger="interval",
        minutes=10,
        id="reconciler",
        name="Outage Reconciler",
    )

    scheduler.add_job(
        lambda: jobs.degraded_job(rt),
        trigger="interval",
        minutes=conf.connectivity.degraded_window_minutes,
        id="degraded",
        name="Degraded Period Detector",
        # Early first run so an empty table backfills right away.
        next_run_time=now() + timedelta(seconds=30),
    )

    scheduler.add_job(
        lambda: cleanup.prune_pings(rt.engine),
        trigger="interval",
        hours=24,
        id="cleanup",
        name="Ping Retention Cleanup",
        # First run shortly after startup: if the process restarts more often
        # than the 24h interval, retention would otherwise never run.
        next_run_time=now() + timedelta(minutes=5),
    )

    rt.scheduler = scheduler

    log.info(
        "Scheduler started — pinger every %ds, speed test every %.1fh, "
        "reconciler every 10min, cleanup daily.",
        conf.connectivity.ping_interval_seconds,
        conf.speed_test.interval_hours,
    )

    scheduler.start()


if __name__ == "__main__":
    main()
