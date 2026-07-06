"""
Scheduled job wrappers, shared by main.py (scheduler wiring) and
dashboard.py (manual trigger endpoint). Kept out of main.py because
main imports dashboard — importing back would be circular.

Each wrapper runs its task and publishes SSE events for the dashboard.
"""

from __future__ import annotations

import logging

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


def speed_test_job(rt: Runtime, force: bool = False) -> None:
    test_id = speed_test.run(rt.engine, rt.conf, scheduled_for=now(), force=force)
    try:
        if test_id is not None:
            history = queries.get_speed_history(rt.engine, days=30)
            if history:
                events.publish("speed_update", history[-1])
        events.publish("speed_test_done", {"ok": test_id is not None})
        _publish_status(rt)
    except Exception as exc:
        log.warning("Failed to publish speed event: %s", exc)
