"""Flask dashboard — read-only views, SSE stream, and settings endpoints."""

from __future__ import annotations

import json
import logging
import queue
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from netmon import config as cfg
from netmon import events, jobs, pinger, queries, report as report_mod, speed_test
from netmon.runtime import Runtime

log = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"


def _settings_snapshot(conf: cfg.Config) -> dict:
    """The dashboard-editable subset of the config."""
    return {
        "target_mbps": conf.target_mbps,
        "speed_test": {
            "interval_hours": conf.speed_test.interval_hours,
            "soft_threshold": conf.speed_test.soft_threshold,
            "hard_threshold": conf.speed_test.hard_threshold,
            "postpone_retry_minutes": conf.speed_test.postpone_retry_minutes,
            "max_postpones": conf.speed_test.max_postpones,
        },
        "connectivity": {
            "ping_interval_seconds": conf.connectivity.ping_interval_seconds,
            "outage_threshold_failures": conf.connectivity.outage_threshold_failures,
            "ping_targets": list(conf.connectivity.ping_targets),
            "degraded_loss_threshold_pct": conf.connectivity.degraded_loss_threshold_pct,
            "degraded_window_minutes": conf.connectivity.degraded_window_minutes,
        },
    }


def create_app(rt: Runtime) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(_TEMPLATE_DIR),
        static_folder=str(_STATIC_DIR),
    )

    # Suppress Flask request logs — APScheduler logs are enough noise.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    @app.route("/")
    def index():
        return render_template(
            "index.html",
            target_mbps=rt.conf.target_mbps,
            settings=_settings_snapshot(rt.conf),
        )

    # ------------------------------------------------------------------
    # REST endpoints
    # ------------------------------------------------------------------

    @app.route("/api/status")
    def api_status():
        return jsonify({
            **queries.get_status(rt.engine),
            "test_running": speed_test.is_running(),
        })

    @app.route("/api/speed-history")
    def api_speed_history():
        return jsonify({
            "data": queries.get_speed_history(rt.engine, days=30),
            "events": queries.get_test_events(rt.engine, days=30),
            "target_mbps": rt.conf.target_mbps,
            "interval_hours": rt.conf.speed_test.interval_hours,
        })

    @app.route("/api/outages")
    def api_outages():
        return jsonify(queries.get_outages(rt.engine, days=30))

    @app.route("/api/ping-heatmap")
    def api_ping_heatmap():
        return jsonify(queries.get_ping_heatmap(rt.engine, days=7))

    @app.route("/api/latency-history")
    def api_latency_history():
        return jsonify(queries.get_latency_history(rt.engine, days=7))

    @app.route("/api/daily")
    def api_daily():
        return jsonify(queries.get_daily_summary(rt.engine, days=30))

    @app.route("/api/degraded")
    def api_degraded():
        return jsonify(queries.get_degraded(rt.engine, days=30))

    @app.route("/api/adherence")
    def api_adherence():
        return jsonify(queries.get_adherence(rt.engine))

    @app.route("/api/config")
    def api_config():
        """Read-only config snapshot (kept for compatibility)."""
        conf = rt.conf
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
    # Settings
    # ------------------------------------------------------------------

    @app.route("/api/settings", methods=["GET"])
    def api_settings_get():
        return jsonify({
            "settings": _settings_snapshot(rt.conf),
            "data_cost": queries.get_data_cost(rt.engine),
        })

    @app.route("/api/settings", methods=["POST"])
    def api_settings_post():
        updates = request.get_json(silent=True)
        if not isinstance(updates, dict):
            return jsonify({"ok": False, "error": "Invalid JSON body."}), 400
        try:
            cfg.save_settings(updates)
        except (ValueError, KeyError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        log.info("Settings saved to config.yaml from dashboard.")
        return jsonify({"ok": True, "restart_required": True})

    @app.route("/api/restart", methods=["POST"])
    def api_restart():
        if rt.scheduler is None:
            return jsonify({
                "ok": False,
                "error": "Monitoring is not running in this process "
                         "(dashboard-only mode). Settings were saved to "
                         "config.yaml and will apply on the next full start.",
            }), 409

        try:
            new_conf = cfg.load()
        except (ValueError, KeyError, OSError) as exc:
            return jsonify({"ok": False, "error": f"Reload failed: {exc}"}), 400

        with rt.lock:
            targets = pinger.resolve_targets(new_conf.connectivity.ping_targets)
            if not targets:
                return jsonify({
                    "ok": False,
                    "error": "No ping targets resolved from the new config.",
                }), 400
            pinger.restore_state(rt.engine, targets)

            rt.conf = new_conf
            rt.targets = targets

            rt.scheduler.reschedule_job(
                "pinger", trigger="interval",
                seconds=new_conf.connectivity.ping_interval_seconds,
            )
            rt.scheduler.reschedule_job(
                "speed_test", trigger="interval",
                hours=new_conf.speed_test.interval_hours,
            )
            rt.scheduler.reschedule_job(
                "degraded", trigger="interval",
                minutes=new_conf.connectivity.degraded_window_minutes,
            )

        log.info(
            "Monitoring restarted from dashboard — pinger every %ds, "
            "speed test every %.1fh, targets: %s",
            new_conf.connectivity.ping_interval_seconds,
            new_conf.speed_test.interval_hours,
            targets,
        )
        try:
            events.publish("status_update", queries.get_status(rt.engine))
        except Exception as exc:
            log.warning("Failed to publish post-restart status: %s", exc)

        return jsonify({"ok": True, "settings": _settings_snapshot(new_conf)})

    @app.route("/report")
    def report_page():
        """Self-contained ISP evidence report (print to PDF from the browser)."""
        try:
            days = max(1, min(365, int(request.args.get("days", 30))))
        except ValueError:
            days = 30
        return report_mod.render_report(rt.engine, rt.conf, days=days)

    @app.route("/api/speed-test/run", methods=["POST"])
    def api_run_speed_test():
        if rt.scheduler is None:
            return jsonify({
                "ok": False,
                "error": "Monitoring is not running in this process "
                         "(dashboard-only mode).",
            }), 409
        if speed_test.is_running():
            return jsonify({
                "ok": False,
                "error": "A speed test is already running.",
            }), 409

        rt.scheduler.add_job(
            lambda: jobs.speed_test_job(rt, force=True),
            trigger="date",  # fire once, immediately
            id="speed_test_manual",
            name="Manual Speed Test",
            replace_existing=True,
        )
        log.info("Manual speed test queued from dashboard.")
        return jsonify({"ok": True})

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
