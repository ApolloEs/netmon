# NetMon as a Windows Service (NSSM)

Runs NetMon at boot, no login required, auto-restarts on crashes.

`nssm.exe` (2.24-103, win64) is **bundled in this folder** — verified
byte-for-byte against the official build's published hash (nssm.cc has
regular outages, so don't rely on downloading it). Keep it here: the
service registration points at this copy.

## One-time setup

1. Stop any console instance of NetMon first (frees port 5000).
2. Open an **elevated** PowerShell (right-click → Run as administrator).
3. ```powershell
   cd r:\Projects\Any\netmon\deploy\windows
   powershell -ExecutionPolicy Bypass -File install-service.ps1
   Start-Service NetMon
   ```
4. Open <http://127.0.0.1:5000> and confirm the dashboard is live.
5. Verify a clean stop once: `Stop-Service NetMon` should return within
   ~15 seconds and `netmon.log` should end with "NetMon stopped cleanly."
   Then `Start-Service NetMon` and leave it running.

## Day to day (elevated PowerShell; built-in cmdlets, no nssm needed)

| Action        | Command                 |
|---------------|-------------------------|
| Start         | `Start-Service NetMon`  |
| Stop          | `Stop-Service NetMon`   |
| Restart (after code changes) | `Restart-Service NetMon` |
| Status        | `Get-Service NetMon`    |
| Change config | Dashboard settings modal (its Restart button reloads monitoring — no service restart needed) |
| Uninstall     | `uninstall-service.ps1` (elevated) |

During active development, prefer stopping the service and running
`python -m netmon.main` in a terminal; start the service again when done.

## Where things live

- App log: `netmon.log` in the repo root (rotating, per config.yaml).
- Service console output: `logs\service-out.log` / `logs\service-err.log`.
- The service runs `python -m netmon.main` with the repo as working
  directory, so `config.yaml` is found exactly as in manual runs.

## Notes

- Postgres must be running as a service too (the standard PG installer
  does this by default). NetMon retries DB connections via pool_pre_ping,
  but it must be able to connect at startup.
- Stop uses a console Ctrl+C first (15s grace), which triggers NetMon's
  clean shutdown path; NSSM escalates only if that fails.
