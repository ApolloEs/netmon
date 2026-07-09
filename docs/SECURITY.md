# LineProof Security & Trust Model

LineProof is built for a single household on a trusted LAN, not the open
internet. Access control has two independent layers plus a documented
plan for encryption.

## The two layers

**View — passphrase login.** Set a passphrase with
`python scripts/set_password.py`; it stores only a salted
`werkzeug`/PBKDF2 hash in `config.yaml` under `dashboard.password_hash`
(the plaintext is never written). While a hash is set, any LAN or remote
device must log in before seeing *anything* — dashboard, APIs, or the
evidence report. The PC running LineProof (localhost) always bypasses.
Login sets a signed session cookie (30-day lifetime). Clear the
passphrase with `set_password.py --clear`.

**Edit — device enrollment.** Changing settings, restarting monitoring,
or triggering a speed test additionally requires either localhost or a
device enrolled through the QR flow. On the PC view, **Link device**
mints a one-time, 10-minute enrollment token as a QR code; scanning it
sets a year-long device cookie (value = a per-install secret in the
gitignored `.dashboard-secret` file) **and** logs the device in — so a
phone gets both view and edit from a single scan without ever typing the
passphrase.

Net effect: *login = "may view", enrollment/localhost = "may edit".*

## Revocation

- **All devices:** delete `.dashboard-secret` and restart — every
  enrolled device cookie and every signed session is invalidated at once
  (the session-signing key is derived from that secret).
- **The passphrase:** `set_password.py --clear`, then restart.

There is no per-device revocation — the enrollment secret is shared by
all enrolled devices by design (a deliberate simplification for a home
tool).

## What this does and does not protect

Protects against a casual device on your network — a guest phone, a
smart-TV, an IoT gadget — reading your data or changing your monitoring.
Brute-forcing a decent passphrase against PBKDF2 is infeasible;
`HttpOnly` + `SameSite=Lax` cookies blunt XSS and CSRF.

It does **not** provide confidentiality on the wire: traffic is plain
HTTP, so anyone able to capture your Wi-Fi traffic could read the
passphrase and cookies in transit. That is the job of the next layer.

## Planned next layer — TLS (encryption in transit)

Tracked follow-up, to land alongside the Linux / Raspberry Pi migration
where it's far less friction than on Windows. `waitress` has no native
HTTPS, so the intended approach is to terminate TLS in front of it rather
than in the app:

- **Tailscale** (simplest) — puts every device on an encrypted WireGuard
  network and can serve LineProof over HTTPS with a real cert via
  `tailscale serve`; also removes the need to open any LAN port.
- **Caddy** as a reverse proxy — automatic HTTPS with a locally-trusted
  CA (or a real cert if the Pi has a domain), proxying to
  `127.0.0.1:5000`.
- **nginx + self-signed cert** — works everywhere but shows a browser
  warning until the cert is trusted on each device.

When TLS is in place, the passphrase and cookies stop being
plaintext-on-the-wire and LineProof becomes safe to reach from outside
the LAN. Until then, keep it on the local network only.

## Docker note

The compose preset sets `require_edit_token: false` (a container never
sees a localhost request, so QR enrollment can't work) and publishes the
port loopback-only. Set a passphrase and adjust the port mapping
deliberately if you want LAN access from the container.
