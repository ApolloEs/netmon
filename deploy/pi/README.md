# NetMon on the Raspberry Pi (prepared, not yet deployed)

The production target: Pi 4, Ethernet to the router, always on.
Same codebase; this directory holds the systemd unit and the list of
things that MUST be addressed during the migration.

## Known blockers to fix before/while migrating

1. **Gateway resolution is Windows-only.** `pinger._resolve_gateway()`
   parses `ipconfig`. On Linux it must run `ip route show default` and
   parse the `via <ip>` field. Until fixed, the `gateway` ping target is
   silently dropped.
2. **Unprivileged ICMP needs a sysctl.** icmplib with `privileged=False`
   requires the service user's group in the ping range:
   `sudo sysctl -w net.ipv4.ping_group_range="0 2147483647"`
   (persist in `/etc/sysctl.d/99-netmon.conf`).
3. **Ookla CLI path.** Install the ARM build of the Ookla speedtest CLI
   and update `speed_test.cli_path` in config.yaml.
4. **config.yaml review.** DB URL (local PG on the Pi vs remote),
   log path, and any Windows-specific paths.

## Install sketch

```bash
sudo apt install postgresql python3-venv
git clone <repo> /home/pi/netmon && cd /home/pi/netmon
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# create DB + user, copy config.example.yaml -> config.yaml, edit
.venv/bin/python scripts/init_db.py
.venv/bin/python scripts/migrate.py
sudo cp deploy/pi/netmon.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now netmon
```
