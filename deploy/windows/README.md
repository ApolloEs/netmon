# NetMon as a Windows Service (NSSM)

Runs NetMon at boot, no login required, auto-restarts on crashes.

`nssm.exe` is **not committed** (no binaries in git). The install script
acquires it automatically: it prefers an `nssm.exe` already in this folder
(or on PATH), and otherwise downloads the official 2.24 release from
nssm.cc, **verifies its SHA-256** against the published checksum (the
install aborts loudly on any mismatch), and extracts the win64 exe next to
the script. Once installed, keep `nssm.exe` here: the service registration
points at this copy. (nssm.cc has occasional outages — if the download
fails, fetch the zip yourself later or drop in a verified `nssm.exe` by
hand.)

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
   ~15 seconds and `netmon.log` should end with "LineProof stopped cleanly."
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

## LAN / phone access

To reach the dashboard from other devices (see "View from your phone" in
the main README): set `dashboard.host: 0.0.0.0` in config.yaml, then in
an **elevated** PowerShell:

```powershell
netsh advfirewall firewall add rule name="LineProof Dashboard" dir=in action=allow protocol=TCP localport=5000 remoteip=localsubnet
Restart-Service NetMon
```

(`remoteip=localsubnet` keeps the rule LAN-only. Remove it later with
`netsh advfirewall firewall delete rule name="LineProof Dashboard"`.)

## Notes

- Postgres must be running as a service too (the standard PG installer
  does this by default). NetMon retries DB connections via pool_pre_ping,
  but it must be able to connect at startup.
- Stop uses a console Ctrl+C first (15s grace), which triggers NetMon's
  clean shutdown path; NSSM escalates only if that fails.
