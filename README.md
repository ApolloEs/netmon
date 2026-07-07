# NetMon

**Your ISP promised a speed. NetMon collects the receipts.**

A self-hosted internet connection monitor that runs 24/7 on a home machine,
measures what your line actually delivers, and turns months of data into a
bilingual, print-ready evidence report you can hand to your provider.

![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-lightgrey)

![NetMon dashboard — speed, latency, and daily quality](docs/screenshots/dashboard1.png)
![NetMon dashboard — outage timeline, adherence, and packet loss](docs/screenshots/dashboard2.png)

## Why

Built after paying for 100 Mbps and measuring ~20 — even at 3 AM. Speedtest
screenshots are easy to dismiss; a continuous, methodologically fair dataset
with outage logs and packet-loss records is not.

## What it does

- **Two-layer monitoring**
  - *Connectivity layer*: ICMP probes to anchor hosts (Cloudflare, Google,
    your gateway, optional hostnames for DNS checks) every few seconds —
    detects outages, packet loss, and latency at near-zero bandwidth cost.
  - *Speed layer*: full download/upload/latency measurements via the official
    Ookla Speedtest CLI on a configurable interval.
- **Fair measurements** — speed tests are automatically postponed while the
  line is in use, so results reflect an idle connection (and can't be blamed
  on your own downloads).
- **Outage & degraded-period detection** — sustained drops become outage
  records with durations; periods of sustained packet loss ("up but
  unusable") are detected and persisted separately.
- **Live dashboard** — speed-over-time with outage/degraded bands and no-data
  gaps, latency chart, packet-loss-by-hour heatmap, daily quality calendar,
  outage timeline, target-adherence rollups. Real-time updates over SSE,
  light/dark theme, mobile-friendly.
- **In-dashboard control** — edit monitoring settings (validated, comment-
  preserving), restart monitoring, or force a speed test without touching a
  terminal.
- **Evidence report** — one click renders a self-contained HTML report
  (Greek/English toggle, print-to-PDF): median vs. contracted speed,
  percentiles, peak vs. off-peak comparison, outage log, degraded time,
  packet loss, and a methodology section. Safe to email — everything is
  inlined.
- **Data-cost aware** — real bytes per test are recorded; the settings panel
  projects data usage per day for your current cadence.
- **Set-and-forget deployment** — Windows service via bundled NSSM, systemd
  unit prepared for Raspberry Pi.

## Quick start

Prerequisites: Python 3.10+, PostgreSQL, and the
[Ookla Speedtest CLI](https://www.speedtest.net/apps/cli).

```bash
git clone https://github.com/ApolloEs/netmon.git
cd netmon
pip install -r requirements.txt

# Create the database (once):
#   CREATE USER netmon WITH PASSWORD '...'; CREATE DATABASE netmon OWNER netmon;

cp config.example.yaml config.yaml     # then edit: target speed, DB URL, CLI path
python scripts/init_db.py              # create tables
python scripts/migrate.py              # idempotent schema extras

python -m netmon.main                  # start monitoring + dashboard
```

Dashboard: <http://127.0.0.1:5000> · Evidence report: the **📄 Report**
button, or `python scripts/generate_report.py --days 30`.

## Run it as a service

- **Windows**: [`deploy/windows/`](deploy/windows/README.md) — install script
  with bundled NSSM, graceful stop, crash restart.
- **Raspberry Pi / Linux**: [`deploy/pi/`](deploy/pi/README.md) — systemd
  unit and migration checklist.

## Configuration

Everything lives in `config.yaml` (see
[`config.example.yaml`](config.example.yaml)). Highlights:

| Key | Meaning |
|---|---|
| `target_mbps` | The speed your contract promises — every stat is relative to this |
| `speed_test.interval_hours` | Cadence of full speed tests (mind the data cost) |
| `speed_test.soft/hard_threshold` | Postpone/skip tests when the line is already in use |
| `connectivity.ping_interval_seconds` | Probe cadence (outage detection resolution) |
| `connectivity.degraded_loss_threshold_pct` | Packet-loss level that counts as "degraded" |
| `report.*` | Optional identity lines printed on the evidence report |

Monitoring-related settings are also editable from the dashboard's ⚙ panel,
with live restart — no service interruption.

## How it's built

Python · PostgreSQL · APScheduler · Flask + waitress · Chart.js (vendored —
the dashboard keeps working *during* outages). Raw pings are kept 7 days
(aggregates and detected periods are kept forever), so the database stays
small indefinitely.

See [`CLAUDE.md`](CLAUDE.md) for the original design document and
[`KNOWN_ISSUES.md`](KNOWN_ISSUES.md) for the honest list of rough edges.

## Roadmap

Raspberry Pi migration (always-on, low-power) · outage alerts (Discord) ·
weekly email digest · per-hour speed targets.

## License

[MIT](LICENSE)
