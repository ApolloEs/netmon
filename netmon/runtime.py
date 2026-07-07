"""
Shared mutable runtime state.

Jobs and dashboard endpoints read config through this object at call time
(instead of capturing a frozen Config at startup), which is what allows
the dashboard's "restart monitoring" to swap in a freshly loaded config
without restarting the process.
"""

from __future__ import annotations

import threading
from typing import List, Optional

from sqlalchemy.engine import Engine

from netmon import config as cfg
from netmon.pinger import PingerState


class Runtime:
    def __init__(self, engine: Engine, conf: cfg.Config):
        self.engine = engine
        self.conf = conf
        self.targets: List[str] = []
        self.scheduler = None  # set by main.py; None in dashboard-only mode
        self.pinger_state = PingerState()  # replaced via restore_state() at startup/restart
        self.lock = threading.Lock()
