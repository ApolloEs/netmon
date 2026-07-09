"""
Device enrollment for LAN dashboard access.

The dashboard is read-only for devices on the LAN unless they hold the
trusted-device cookie. The PC view (localhost) mints a one-time, short-
lived enrollment token, shown as a QR code; opening the encoded link
sets the cookie. The cookie value is a single per-install secret,
persisted in a gitignored file — delete the file and restart to revoke
every enrolled device.

Pure logic, no Flask imports — see tests/test_deviceauth.py.
"""

from __future__ import annotations

import hmac
import logging
import secrets
import time
from pathlib import Path
from typing import Dict, Optional

log = logging.getLogger(__name__)

TOKEN_TTL_SECONDS = 600  # enrollment QR is valid for 10 minutes


class DeviceAuth:
    def __init__(self, secret_path: Path):
        self._secret_path = Path(secret_path)
        self._secret = self._load_or_create_secret()
        # one-time enrollment tokens → expiry timestamp
        self._tokens: Dict[str, float] = {}

    def _load_or_create_secret(self) -> str:
        try:
            existing = self._secret_path.read_text().strip()
            if existing:
                return existing
        except FileNotFoundError:
            pass
        secret = secrets.token_urlsafe(32)
        self._secret_path.write_text(secret)
        log.info("Created new dashboard device secret at %s", self._secret_path)
        return secret

    def mint_token(self, now: Optional[float] = None) -> str:
        """One-time enrollment token, valid TOKEN_TTL_SECONDS."""
        now = time.time() if now is None else now
        self._tokens = {t: exp for t, exp in self._tokens.items() if exp > now}
        token = secrets.token_urlsafe(16)
        self._tokens[token] = now + TOKEN_TTL_SECONDS
        return token

    def redeem(self, token: str, now: Optional[float] = None) -> Optional[str]:
        """Exchange a valid token for the device secret. Single-use."""
        now = time.time() if now is None else now
        expiry = self._tokens.pop(token, None)
        if expiry is None or expiry <= now:
            return None
        return self._secret

    def is_trusted(self, cookie_value: Optional[str]) -> bool:
        if not cookie_value:
            return False
        return hmac.compare_digest(cookie_value, self._secret)
