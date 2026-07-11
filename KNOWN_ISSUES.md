# Known Issues & Deferred Work

Items identified during code review but not yet addressed.

---

## bandwidth.py

**Interface selection heuristic is fragile.**
`_best_interface()` picks the NIC with the most cumulative traffic. On a
machine with a VPN adapter, Docker bridge, or WSL virtual NIC, it may pick
the wrong one. Fix: add an optional `interface_name` key to config.yaml that
overrides auto-selection when set.

---

## speed_test.py

**Bandwidth sample interval is hardcoded.**
The 5-second sample in `run()` is not configurable. Low priority, but could
be a `bandwidth_sample_seconds` key under `speed_test` in config.yaml.

---

## dashboard / jobs

**Status query runs even with no SSE clients.**
`pinger_job` calls `queries.get_status()` every cycle to publish an event
nobody may be listening to. Fix: add an `events.has_subscribers()` guard.
(Cheaper now that the composite ping index exists, but still wasted work.)

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
`ended_at` earlier than `started_at`. Outside the dashboard's 30-day
window, causes no UI bug. Left in place deliberately — do not delete
DB rows without an explicit request.

**One speed test has a garbage upload value (2026-07-10 18:53 UTC).**
During a near-outage (download 0.62 Mbps) the Ookla CLI returned
`INT64_MIN` (-9223372036854775808) as `upload.bandwidth`, which became
`upload_mbps = -73786976294838.2`. Future writes are now guarded
(`speed_test._sane_mbps` stores NULL for impossible values), but this
existing row was left untouched (no DB edits without an explicit
request). Visible only in the chart tooltip; the point is clipped by the
y-axis floor at 0.

---

## Raspberry Pi migration (v2)

See `deploy/pi/README.md` for the remaining checklist:
`ping_group_range` sysctl, ARM Ookla CLI, config review.
(Gateway resolution on Linux is done.)
