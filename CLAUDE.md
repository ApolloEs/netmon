# LineProof — Claude Code Operational Guide

Self-hosted internet connection monitor: ICMP pinger + Ookla speed tests
→ PostgreSQL → Flask dashboard + printable ISP evidence report.

**Naming (deliberate, not mid-rename):** the project/distribution/command
is `lineproof` and all user-facing strings (logs, dashboard, report, CLI)
say LineProof; the import package stays `netmon` (the `pillow` → `PIL`
pattern), and the Postgres user/database and the Windows service are also
still `netmon`/`NetMon` — kept so the long-running production instance
needs no data migration or service re-registration.

The full design rationale (two-layer monitoring, bandwidth-aware
postponement, data model, build order) is in
[`docs/DESIGN.md`](docs/DESIGN.md) — read it before structural changes.
The as-built data flow is in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
Rough edges are tracked honestly in [`KNOWN_ISSUES.md`](KNOWN_ISSUES.md).

## Project map

| Path | What |
|---|---|
| `netmon/main.py` | Entry point: scheduler wiring, dashboard thread, signal handling |
| `netmon/pinger.py` | Layer 1: ICMP pings, streak/outage state (`PingerState`), gateway resolution |
| `netmon/speed_test.py` | Layer 2: Ookla CLI wrapper, postpone/skip/force decision |
| `netmon/bandwidth.py` | psutil throughput sampling (pre-test check) |
| `netmon/outage_detector.py` | Startup/periodic reconciler for stale outage rows |
| `netmon/degraded.py` | Degraded-period detection (sustained packet loss windows) |
| `netmon/cleanup.py` | 7-day raw-ping retention pruning |
| `netmon/db.py` | SQLAlchemy Core tables, `ensure_schema()` (auto create+migrate) |
| `netmon/config.py` | YAML load/validate + comment-preserving `save_settings` (ruamel) |
| `netmon/runtime.py` | Shared mutable state (`Runtime`) enabling in-process restart |
| `netmon/jobs.py` | Scheduled job wrappers + SSE publishing (breaks main↔dashboard cycle) |
| `netmon/dashboard.py` | Flask routes incl. `/api/stream` SSE |
| `netmon/queries.py` | All dashboard SQL — aggregate in SQL, never in Python |
| `netmon/report.py` + `templates/report.html` | Bilingual (EL/EN) evidence report |
| `scripts/` | init/migrate/smoke-test/report utilities (not collected by pytest) |
| `deploy/windows/`, `deploy/pi/` | NSSM service scripts; systemd unit |
| `tests/` | Pure-logic unit tests — no live DB, no network |

## Run / test / lint

```bash
python -m netmon.main            # full app (needs config.yaml + Postgres)
python scripts/run_dashboard.py  # dashboard only, read-only mode
python -m pytest -q              # unit tests (no DB needed)
python -m ruff check .           # lint (config in pyproject.toml)
docker compose up -d             # containerized stack (see README)
```

## Environment gotchas (Apollo's dev machine)

- **A live NSSM service (`NetMon`) runs this code 24/7** and owns port
  5000 and the production DB. Check `Get-Service NetMon` before starting
  a console instance; use `Restart-Service NetMon` (elevated) to pick up
  code changes.
- Avast intercepts TLS (breaks cert verification inside containers and
  fresh-venv pip) and silently deletes loose `nssm.exe` copies.
- Windows PowerShell 5.1: commit via `git commit -F <file>` — inline
  multiline messages get mangled.

## Conventions

- Conventional commits (`feat:`/`fix:`/`chore:`/`docs:`/`test:`/`ci:`),
  one commit per agreed milestone. **Always show Apollo the full commit
  message and get approval before committing.**
- **Never mutate the database directly** (no manual UPDATE/DELETE of
  rows) unless explicitly requested. Outage #1's negative duration is
  deliberate test-data history — leave it.
- A problem with no visible dashboard impact gets **recorded in
  KNOWN_ISSUES.md, not silently fixed**; resolve KNOWN_ISSUES items in
  the same commit as the fix.
- Aggregation happens in SQL (`queries.py`), timestamps are tz-aware
  UTC end to end, config edits must preserve YAML comments.
- Suggest → discuss → code → review together. Direct, skimmable
  explanations; "CC" = Claude Code.
