"""
In-process pub/sub event queue for real-time dashboard updates.

The pinger and speed test jobs publish events here after each run.
The SSE endpoint in dashboard.py subscribes and forwards to the browser.
"""

from __future__ import annotations

import queue
import threading
from typing import Any

_lock = threading.Lock()
_subscribers: list[queue.Queue] = []


def subscribe() -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=100)
    with _lock:
        _subscribers.append(q)
    return q


def unsubscribe(q: queue.Queue) -> None:
    with _lock:
        try:
            _subscribers.remove(q)
        except ValueError:
            pass


def publish(event_type: str, data: dict[str, Any]) -> None:
    """Publish an event to all connected SSE clients."""
    payload = {"type": event_type, **data}
    with _lock:
        dead = []
        for q in _subscribers:
            try:
                q.put_nowait(payload)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _subscribers.remove(q)
