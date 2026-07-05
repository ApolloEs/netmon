# Known Issues & Deferred Work

Items identified during code review but not yet addressed.
Pick these up after the main stages are complete.

---

## Stage 8 checklist (service deployment)

These will definitely bite when NetMon runs as a service — fix them as
part of stage 8.

**Service stop will hang up to ~45 min.**
`speed_test.run()` sleeps `postpone_retry_minutes` inside the job thread
(up to `max_postpones` times), and the shutdown handler calls
`scheduler.shutdown(wait=True)`. NSSM/systemd will hit their stop timeout
and hard-kill the process. Fix: instead of sleeping inside `run()`, have
the scheduler add a one-off retry job (`trigger="date"`) and return
immediately — this also frees the worker thread.

**Relative log path.**
`logging.file: netmon.log` resolves against the service's working
directory (often `C:\Windows\System32` under NSSM). Make it absolute in
config, or resolve relative paths against the repo root in `setup_logging`.

**Speed test fires 60s after every process start.**
A crash-looping or frequently restarted service burns 300–500 MB per
cycle. Fix: read the newest `speed_tests.timestamp` at startup and
schedule the first run at `last + interval` (clamped to now).

**Windows services don't receive SIGTERM.**
The handler covers SIGINT/SIGTERM, but under NSSM the stop signal arrives
as a console Ctrl-C event. Verify NSSM's stop method actually triggers a
graceful shutdown.

**Dashboard is dead exactly when you need it.**
Chart.js and the date adapter load from the jsdelivr CDN — during an
outage the dashboard won't render charts. Vendor both files into
`netmon/static/`.

**Flask dev server in production.**
Fine for dev; for the service, serve the same app with `waitress`
(pure-Python, works on Windows and the Pi).

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

**Bandwidth sample interval is hardcoded.**
The 5-second sample in `run()` is not configurable. Low priority, but could
be a `bandwidth_sample_seconds` key under `speed_test` in config.yaml.

---

## queries.py / dashboard

**`get_status` sorts the whole pings table on every call.**
`DISTINCT ON (target) … ORDER BY target, timestamp DESC` runs every ping
cycle (45s) with only a `timestamp` index — a full sort of ~40k rows/week.
Fix: add a composite index `(target, timestamp DESC)` on `connectivity_pings`.

**Status query runs even with no SSE clients.**
`_pinger_job` calls `queries.get_status()` every cycle to publish an event
nobody may be listening to. Fix: add an `events.has_subscribers()` guard.

**Heatmap buckets by UTC hour.**
The goal is spotting "it's always bad at 8pm" — local time. Fix: aggregate
with `AT TIME ZONE <local tz>` in the heatmap query (and update the label
in index.html).

**Adherence endpoint returns Decimals as strings.**
Flask serializes `numeric` values as strings. Harmless today (display-only),
but cast to float in SQL if the client ever computes on them.

---

## cleanup.py

**Retention period is hardcoded.**
`prune_pings` defaults to 7 days with no config key. Fix: add a
`retention_days` key under a `cleanup`/`connectivity` section.

---

## Data anomalies (pre-fix test data)

**Outage #1 has a negative duration.**
A record from April stage-testing (before the reconciler fix) has
`ended_at` earlier than `started_at`, giving a duration of about
−258,000s. It sits outside the dashboard's 30-day window so it is not
visible and causes no UI bug. Left in place deliberately — do not delete
DB rows without an explicit request. Cause (force-closing open outages)
is already fixed in `outage_detector.reconcile()`.

---

## General

**No Pi deployment path yet.**
CLAUDE.md notes the eventual target is a Raspberry Pi 4 with a systemd unit.
When that migration happens, these need attention:
- gateway resolution (see pinger.py above)
- systemd unit instead of NSSM
- `icmplib` unprivileged mode requires the `net.ipv4.ping_group_range`
  sysctl to include the service user's group on Linux
- any Windows-specific paths in config.yaml
