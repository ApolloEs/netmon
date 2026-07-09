"""
Tests for the passphrase login gate, exercised through Flask's test
client. A non-local REMOTE_ADDR is used so the localhost bypass doesn't
mask the gate; no database is touched (the gate runs before any handler
that would query it).
"""

from types import SimpleNamespace

import pytest
from werkzeug.security import generate_password_hash

from netmon import config as cfg
from netmon.dashboard import DEVICE_COOKIE, create_app

LAN = {"REMOTE_ADDR": "192.168.1.50"}
PASSWORD = "correct horse"


def make_conf(password_hash=""):
    return SimpleNamespace(
        target_mbps=100,
        speed_test=SimpleNamespace(
            interval_hours=3, soft_threshold=0.5, hard_threshold=0.85,
            postpone_retry_minutes=15, max_postpones=3,
        ),
        connectivity=SimpleNamespace(
            ping_interval_seconds=45, outage_threshold_failures=3,
            ping_targets=["1.1.1.1"], degraded_loss_threshold_pct=5,
            degraded_window_minutes=10,
        ),
        dashboard=SimpleNamespace(
            host="0.0.0.0", port=5000, require_edit_token=True,
            password_hash=password_hash,
        ),
    )


@pytest.fixture
def client_factory(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "CONFIG_PATH", tmp_path / "config.yaml")

    def make(password_hash=""):
        rt = SimpleNamespace(conf=make_conf(password_hash), engine=None, scheduler=None)
        app = create_app(rt)
        app.config["TESTING"] = True
        return app.test_client(), tmp_path

    return make


def test_no_password_means_no_gate(client_factory):
    client, _ = client_factory()  # auth disabled
    assert client.get("/", environ_base=LAN).status_code == 200


def test_lan_blocked_without_login(client_factory):
    client, _ = client_factory(generate_password_hash(PASSWORD))
    resp = client.get("/", environ_base=LAN)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_api_returns_401_not_redirect(client_factory):
    client, _ = client_factory(generate_password_hash(PASSWORD))
    resp = client.get("/api/status", environ_base=LAN)
    assert resp.status_code == 401
    assert resp.get_json()["login_required"] is True


def test_localhost_bypasses_gate(client_factory):
    client, _ = client_factory(generate_password_hash(PASSWORD))
    # Default test-client REMOTE_ADDR is 127.0.0.1.
    assert client.get("/").status_code == 200


def test_wrong_password_rejected(client_factory):
    client, _ = client_factory(generate_password_hash(PASSWORD))
    resp = client.post("/login", data={"password": "nope"}, environ_base=LAN)
    assert resp.status_code == 401
    assert client.get("/", environ_base=LAN).status_code == 302


def test_correct_password_grants_session(client_factory):
    client, _ = client_factory(generate_password_hash(PASSWORD))
    resp = client.post("/login", data={"password": PASSWORD}, environ_base=LAN)
    assert resp.status_code == 302
    # Session cookie now carried by the client → LAN view allowed.
    assert client.get("/", environ_base=LAN).status_code == 200


def test_logout_clears_session(client_factory):
    client, _ = client_factory(generate_password_hash(PASSWORD))
    client.post("/login", data={"password": PASSWORD}, environ_base=LAN)
    client.get("/logout", environ_base=LAN)
    assert client.get("/", environ_base=LAN).status_code == 302


def test_enrolled_device_counts_as_logged_in(client_factory):
    client, tmp_path = client_factory(generate_password_hash(PASSWORD))
    secret = (tmp_path / ".dashboard-secret").read_text().strip()
    client.set_cookie(DEVICE_COOKIE, secret, domain="localhost")
    # Enrolled device cookie alone (no login session) lets the LAN view load.
    assert client.get("/", environ_base=LAN).status_code == 200


def test_login_redirect_target_stays_relative(client_factory):
    client, _ = client_factory(generate_password_hash(PASSWORD))
    resp = client.post(
        "/login?next=https://evil.example/x",
        data={"password": PASSWORD}, environ_base=LAN,
    )
    assert resp.headers["Location"].endswith("/")
