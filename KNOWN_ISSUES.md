# Known Issues & Deferred Work

Items identified during code review but not yet addressed.

---

## Stage 8 — one manual step remaining

The service scripts are in `deploy/windows/`. What's left is running the
install in an elevated shell and verifying the stop path once:
`install-service.ps1` → `nssm start NetMon` → dashboard loads →
`nssm stop NetMon` returns within ~15s and netmon.log ends with
"NetMon stopped cleanly." (NSSM sends console Ctrl+C → SIGINT handler.)

---

## pinger.py

**Gateway resolution is Windows-only.**
`_resolve_gateway()` parses `ipconfig` output. On the Raspberry Pi (Linux),
this will fail silently and drop the gateway target. Fix: detect the platform
and run `ip route show default`, parsing the `via <ip>` field.
(Also listed in deploy/pi/README.md as a migration blocker.)

**Ping targets are checked sequentially.**
With 4 targets and a 2s timeout each, a full-failure cycle takes up to 8s.
At short ping intervals this eats into the cycle. Fix: run pings
concurrently with `threading` or `asyncio`.

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

---

## Raspberry Pi migration (v2)

See `deploy/pi/README.md` for the full checklist: gateway resolution,
`ping_group_range` sysctl, ARM Ookla CLI, config review.
