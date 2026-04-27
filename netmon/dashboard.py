"""Flask dashboard — read-only views and SSE stream."""

from __future__ import annotations

import json
import logging
import queue
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, stream_with_context
from sqlalchemy.engine import Engine

from netmon import events, queries
from netmon import config as cfg

log = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"


def create_app(engine: Engine, conf: cfg.Config) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(_TEMPLATE_DIR),
        static_folder=str(_STATIC_DIR),
    )

    # Suppress Flask request logs — APScheduler logs are enough noise.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    @app.route("/")
    def index():
        return render_template("index.html", target_mbps=conf.target_mbps)

    # ------------------------------------------------------------------
    # REST endpoints
    # ------------------------------------------------------------------

    @app.route("/api/status")
    def api_status():
        return jsonify(queries.get_status(engine))

    @app.route("/api/speed-history")
    def api_speed_history():
        return jsonify({
            "data": queries.get_speed_history(engine, days=30),
            "events": queries.get_test_events(engine, days=30),
            "target_mbps": conf.target_mbps,
        })

    @app.route("/api/outages")
    def api_outages():
        return jsonify(queries.get_outages(engine, days=30))

    @app.route("/api/ping-heatmap")
    def api_ping_heatmap():
        return jsonify(queries.get_ping_heatmap(engine, days=7))

    @app.route("/api/adherence")
    def api_adherence():
        return jsonify(queries.get_adherence(engine))

    @app.route("/api/config")
    def api_config():
        """Read-only config snapshot. Editing is a v2 feature."""
        return jsonify({
            "target_mbps": conf.target_mbps,
            "ping_interval_seconds": conf.connectivity.ping_interval_seconds,
            "outage_threshold_failures": conf.connectivity.outage_threshold_failures,
            "speed_test_interval_hours": conf.speed_test.interval_hours,
            "soft_threshold": conf.speed_test.soft_threshold,
            "hard_threshold": conf.speed_test.hard_threshold,
            "max_postpones": conf.speed_test.max_postpones,
        })

    # ------------------------------------------------------------------
    # SSE stream
    # ------------------------------------------------------------------

    @app.route("/api/stream")
    def api_stream():
        q = events.subscribe()

        def generate():
            try:
                yield "data: {\"type\": \"heartbeat\"}\n\n"
                while True:
                    try:
                        event = q.get(timeout=25)
                        yield f"data: {json.dumps(event, default=str)}\n\n"
                    except queue.Empty:
                        # Keep-alive heartbeat so the connection stays open.
                        yield "data: {\"type\": \"heartbeat\"}\n\n"
            finally:
                events.unsubscribe(q)

        return Response(
            stream_with_context(generate()),
            content_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    return app
