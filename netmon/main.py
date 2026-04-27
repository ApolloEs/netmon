"""
NetMon entry point — wires all modules into a long-running scheduled process.

Usage:
    python -m netmon.main
    python -m netmon.main --config path/to/config.yaml
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from datetime import timedelta
from pathlib import Path

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.blocking import BlockingScheduler

from netmon import config as cfg
from netmon import cleanup, db, outage_detector, pinger, speed_test
from netmon.utils import now, setup_logging

log = logging.getLogger(__name__)


def _build_scheduler() -> BlockingScheduler:
    return BlockingScheduler(
        executors={"default": ThreadPoolExecutor(max_workers=5)},
        job_defaults={
            "coalesce": True,       # if runs were missed, fire once not many
            "max_instances": 1,     # never run the same job concurrently
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
        log.info("Received %s — shutting down scheduler (waiting for running jobs).", name)
        scheduler.shutdown(wait=True)
        log.info("NetMon stopped cleanly.")
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_shutdown)
    try:
        signal.signal(signal.SIGTERM, handle_shutdown)
    except (OSError, ValueError):
        # SIGTERM may not be available on all platforms (e.g. Windows quirks).
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="NetMon — internet connection monitor.")
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args()

    conf = cfg.load(args.config) if args.config else cfg.load()

    setup_logging(
        level=conf.logging.level,
        log_file=conf.logging.file,
        max_bytes=conf.logging.max_bytes,
        backup_count=conf.logging.backup_count,
    )

    log.info("NetMon starting up.")

    engine = db.make_engine(conf.database.url)

    # Reconcile before new data starts flowing so no zombie outages exist.
    log.info("Running startup outage reconcile...")
    outage_detector.reconcile(engine, conf)

    # Resolve targets once — shared across all pinger calls.
    targets = pinger.resolve_targets(conf.connectivity.ping_targets)
    if not targets:
        log.error("No ping targets resolved — cannot start pinger. Check config.")
        sys.exit(1)

    # Restore pinger streak/outage state from DB after any previous run.
    pinger.restore_state(engine, targets)

    scheduler = _build_scheduler()
    _register_listeners(scheduler)
    _register_signal_handlers(scheduler)

    # --- Pinger: run immediately, then every ping_interval_seconds ---
    scheduler.add_job(
        lambda: pinger.run_once(engine, conf, targets),
        trigger="interval",
        seconds=conf.connectivity.ping_interval_seconds,
        id="pinger",
        name="Connectivity Pinger",
        next_run_time=now(),
    )

    # --- Speed test: first run after 60s, then every interval_hours ---
    scheduler.add_job(
        lambda: speed_test.run(engine, conf, scheduled_for=now()),
        trigger="interval",
        hours=conf.speed_test.interval_hours,
        id="speed_test",
        name="Speed Test",
        next_run_time=now() + timedelta(seconds=60),
    )

    # --- Reconciler: every 10 minutes ---
    scheduler.add_job(
        lambda: outage_detector.reconcile(engine, conf),
        trigger="interval",
        minutes=10,
        id="reconciler",
        name="Outage Reconciler",
    )

    # --- Cleanup: once daily ---
    scheduler.add_job(
        lambda: cleanup.prune_pings(engine),
        trigger="interval",
        hours=24,
        id="cleanup",
        name="Ping Retention Cleanup",
        next_run_time=now() + timedelta(hours=24),  # don't prune on first startup
    )

    log.info(
        "Scheduler started — pinger every %ds, speed test every %.1fh, "
        "reconciler every 10min, cleanup daily.",
        conf.connectivity.ping_interval_seconds,
        conf.speed_test.interval_hours,
    )

    scheduler.start()  # blocks until shutdown


if __name__ == "__main__":
    main()
