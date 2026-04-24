# NetMon — Internet Connection Monitor

## Project Goal

Track home internet speed, latency, and packet loss throughout the day. Compare measured speeds against a user-configured target, detect outages (including duration), and surface the data through a local Flask dashboard. Runs unattended on a home machine; designed for long-term passive operation with minimal maintenance.

## Why This Exists

ISP-promised 100 Mbps but observed ~20 Mbps even during off-peak hours (2–5 AM). Intermittent packet loss. Need evidence over time — both to understand the pattern and to have hard data when confronting the ISP.

## Architectural Decisions

### Two-layer monitoring

**Layer 1 — connectivity pinger** runs every 30–60s. Lightweight ICMP pings to anchor hosts (1.1.1.1, 8.8.8.8, local gateway). Detects outages and packet loss at near-zero bandwidth cost.

**Layer 2 — full speed test** runs every 2–3h (configurable). Uses the Ookla `speedtest` CLI to measure download/upload/ping/jitter. Costs ~300–500 MB per run.

The two layers have very different cadences and costs, which is why they're split. Outage detection needs to be frequent and cheap; bandwidth measurement is expensive and only needs periodic sampling.

### Bandwidth-aware postponement (Layer 2)

Before running a speed test, sample the interface throughput via `psutil.net_io_counters` for ~5 seconds, then decide:

- If current download > `soft_threshold` × target (default 0.5): **postpone**, retry in `postpone_retry_minutes`.
- If current download > `hard_threshold` × target (default 0.85): **skip entirely**, log as "skipped".
- After `max_postpones` consecutive postpones (default 3): **force-run** so we don't get blind spots.

All postponement/skip events are logged as first-class data (they're useful signals about usage patterns, not just noise).

### Stack

- **Language:** Python
- **Speed test:** Ookla `speedtest` CLI (free for personal use, more accurate than `speedtest-cli`). Invoke with `--accept-license --accept-gdpr -f json`.
- **Pings:** `icmplib` (cross-platform, no subprocess overhead).
- **Bandwidth sampling:** `psutil`.
- **Storage:** PostgreSQL.
- **Scheduler:** APScheduler.
- **Web:** Flask + Chart.js (or Plotly) for the dashboard.
- **Config:** YAML.

### Deployment plan

**Development** happens on Apollo's Windows 10 machine. Once stable, wrap as a Windows Service via NSSM so it runs at boot without requiring login.

**Production target:** Raspberry Pi 4 with Ethernet to the router. Low-power, always-on, fits the "build once, leave running" philosophy. Same code; different service wrapper (systemd unit instead of NSSM).

## Data Model (initial)

```sql
-- Full speed test results
CREATE TABLE speed_tests (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    download_mbps REAL,
    upload_mbps REAL,
    ping_ms REAL,
    jitter_ms REAL,
    packet_loss_pct REAL,
    target_mbps REAL NOT NULL,
    pct_of_target REAL,
    server_id TEXT,
    server_name TEXT
);

-- Test lifecycle events (completed, postponed, skipped, forced)
CREATE TABLE test_events (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL,  -- completed | postponed | skipped | forced
    scheduled_for TIMESTAMPTZ,
    current_throughput_mbps REAL,
    reason TEXT,
    retry_count INT DEFAULT 0,
    speed_test_id INT REFERENCES speed_tests(id)
);

-- Outage records
CREATE TABLE outages (
    id SERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    duration_seconds INT,
    trigger TEXT  -- which anchor failed
);

-- Raw connectivity pings (rolling 7-day retention)
CREATE TABLE connectivity_pings (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    target TEXT NOT NULL,
    success BOOLEAN NOT NULL,
    latency_ms REAL
);
CREATE INDEX idx_pings_timestamp ON connectivity_pings(timestamp);
```

## Config shape (initial)

```yaml
target_mbps: 100

speed_test:
  interval_hours: 3
  soft_threshold: 0.5      # postpone if current use > 50% of target
  hard_threshold: 0.85     # skip if current use > 85% of target
  postpone_retry_minutes: 15
  max_postpones: 3

connectivity:
  ping_interval_seconds: 45
  outage_threshold_failures: 3   # consecutive failures before declaring outage
  ping_targets:
    - 1.1.1.1
    - 8.8.8.8
    - gateway              # resolved at runtime

database:
  url: postgresql://netmon:password@localhost:5432/netmon

dashboard:
  host: 127.0.0.1
  port: 5000
```

## Dashboard Views (v1)

- Current status strip: online/offline, last speed, current latency.
- Speed-over-time graph with a horizontal line at target.
- % of target adherence rollup (daily/weekly) — e.g., "you got ≥80% of target 42% of the time".
- Outage timeline (Gantt-style bars, last 7 / 30 days).
- Packet loss heatmap by hour-of-day (surfaces "it's always bad at 8pm").
- Secondary annotations on speed chart marking postponed/skipped tests.

## Build Order

Each stage is independently testable. Do not skip ahead.

1. **Config + DB schema.** YAML loader, SQLAlchemy models (or raw SQL migrations — Apollo's call). Verify connection to Postgres. No scheduler yet.
2. **Connectivity pinger.** Standalone module that pings anchors and writes to `connectivity_pings`. Run manually, confirm records appear.
3. **Bandwidth sampler.** Helper module using `psutil`. Just prints current throughput to console initially.
4. **Speed test runner.** Wrapper around Ookla CLI with JSON output. Integrates the bandwidth sampler for the pre-test postpone/skip decision. Writes to `speed_tests` + `test_events`.
5. **Outage detector.** Reads recent `connectivity_pings`, opens/closes records in `outages`.
6. **Scheduler wiring.** APScheduler runs the pinger and speed-test runner at their configured intervals.
7. **Flask dashboard.** All views listed above. Chart.js for graphs.
8. **Service deployment.** NSSM wrapper on Windows; systemd unit prepared for the Pi.

## Later (v2+)

- Per-hour target speeds (different targets for different times of day).
- Weekly email digest (reuse SMTP patterns from the domain flipper project).
- Discord webhook alerts for outages.
- Per-process bandwidth visibility (Windows requires WMI/ETW — non-trivial).
- Migration from Windows dev machine to Raspberry Pi.

## Working Preferences (Apollo)

- **Workflow:** Suggest approach → discuss it → CC writes the code → review together. This project is optimizing for shipping speed, not hands-on coding practice.
- **"CC"** is the preferred abbreviation for Claude Code.
- **Direct, skimmable explanations.** Prose where it reads well; structured lists only when genuinely listy.
- **Windows 10** dev environment. Git locally, no remote yet.
- **Postgres** for storage, not SQLite.
- **Compound-interest tooling.** Build once, leave running, minimal ongoing maintenance.
