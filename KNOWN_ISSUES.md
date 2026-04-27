# Known Issues & Deferred Work

Items identified during code review but not yet addressed.
Pick these up after the main stages are complete.

---

## pinger.py

**Gateway resolution is Windows-only.**
`_resolve_gateway()` parses `ipconfig` output. On the Raspberry Pi (Linux),
this will fail silently and drop the gateway target. Fix: detect the platform
and run `ip route show default` on Linux, parsing the `via <ip>` field.

**Ping targets are checked sequentially.**
With 3 targets and a 2s timeout each, a full-failure cycle takes up to 6s.
At a 45s interval this is fine, but if more targets are added it eats into
the interval. Fix: run pings concurrently with `threading` or `asyncio`.

---

## bandwidth.py

**Interface selection heuristic is fragile.**
`_best_interface()` picks the NIC with the most cumulative traffic. On a
machine with a VPN adapter, Docker bridge, or WSL virtual NIC, it may pick
the wrong one. Fix: add an optional `interface_name` key to config.yaml that
overrides auto-selection when set.

---

## speed_test.py

**No retry on transient CLI failure.**
If the Ookla CLI exits non-zero due to a momentary glitch (not a real outage),
the runner writes an `error` event and gives up. A single silent retry before
writing the error event would reduce noise in the dashboard.

**Postpone sleep blocks the scheduler thread.**
`time.sleep(postpone_retry_minutes * 60)` holds a thread in APScheduler's
pool for up to 15 minutes per postpone cycle. Under high postpone frequency
this could starve the pinger. Fix: instead of sleeping inside `run()`, have
the scheduler re-schedule a one-off retry job and return immediately.

**Bandwidth sample interval is hardcoded.**
The 5-second sample in `run()` is not configurable. Low priority, but could
be a `bandwidth_sample_seconds` key under `speed_test` in config.yaml.

---

## outage_detector.py

**No age limit on open outages.**
The reconciler processes all open outages regardless of how old they are.
A record left open due to a data anomaly months ago will be re-processed
every reconcile cycle. Fix: skip (and log a warning for) outages older than
a configurable threshold (e.g. 24h) without pings, or close them with a
`force_closed` reason.

---

## config.py

**No value validation.**
The loader accepts logically invalid configs without complaint — e.g.
`soft_threshold > hard_threshold`, `target_mbps: 0` (causes ZeroDivisionError
later), or an empty `ping_targets` list (pinger starts and does nothing).
Fix: add a `validate()` function called at the end of `load()` that raises
a descriptive `ValueError` for each invalid combination.

---

## General

**Graceful shutdown (SIGTERM / SIGINT).**
No signal handlers are registered. When NSSM or systemd stops the service,
the process is killed mid-cycle with no cleanup. Fix: in stage 6 (scheduler),
register a SIGTERM handler that calls `scheduler.shutdown(wait=False)` and
sets a stop flag for `run_loop()`.

**No Pi deployment path yet.**
CLAUDE.md notes the eventual target is a Raspberry Pi 4 with a systemd unit.
When that migration happens: gateway resolution, service wrapper, and any
Windows-specific paths in config.yaml will need attention.
