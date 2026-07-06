"""
Scheduled job wrappers, shared by main.py (scheduler wiring) and
dashboard.py (manual trigger endpoint). Kept out of main.py because
main imports dashboard — importing back would be circular.

Each wrapper runs its task and publishes SSE events for the dashboard.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from netmon import events, pinger, queries, speed_test
from netmon.runtime import Runtime
from netmon.utils import now

log = logging.getLogger(__name__)


def _publish_status(rt: Runtime) -> None:
    events.publish("status_update", {
        **queries.get_status(rt.engine),
        "test_running": speed_test.is_running(),
    })


def pinger_job(rt: Runtime) -> None:
    pinger.run_once(rt.engine, rt.conf, rt.targets)
    try:
        _publish_status(rt)
    except Exception as exc:
        log.warning("Failed to publish ping event: %s", exc)


def speed_test_job(rt: Runtime, force: bool = False, retry_count: int = 0) -> None:
    status, test_id = speed_test.run(
        rt.engine, rt.conf, scheduled_for=now(), force=force, retry_count=retry_count
    )

    # Postponement is handled here, not by sleeping inside run(): schedule a
    # one-off retry so no scheduler thread is parked and shutdown stays fast.
    if status == "postponed" and rt.scheduler is not None:
        retry_minutes = rt.conf.speed_test.postpone_retry_minutes
        next_retry = retry_count + 1
        rt.scheduler.add_job(
            lambda: speed_test_job(rt, retry_count=next_retry),
            trigger="date",
            run_date=now() + timedelta(minutes=retry_minutes),
            id="speed_test_retry",
            name="Speed Test Retry",
            replace_existing=True,
        )
        log.info("Speed test retry %d scheduled in %d min.", next_retry, retry_minutes)

    try:
        if test_id is not None:
            history = queries.get_speed_history(rt.engine, days=30)
            if history:
                events.publish("speed_update", history[-1])
        # postponed/busy runs are not an outcome the Run-test button needs;
        # publishing them would flash a false "failed" state.
        if status not in ("postponed", "busy"):
            events.publish("speed_test_done", {"ok": test_id is not None})
        _publish_status(rt)
    except Exception as exc:
        log.warning("Failed to publish speed event: %s", exc)
