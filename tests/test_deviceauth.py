"""Unit tests for the QR device-enrollment logic (DeviceAuth)."""

from netmon.deviceauth import TOKEN_TTL_SECONDS, DeviceAuth


def make(tmp_path):
    return DeviceAuth(tmp_path / ".dashboard-secret")


def test_secret_file_created_and_stable(tmp_path):
    a = make(tmp_path)
    assert (tmp_path / ".dashboard-secret").is_file()
    b = make(tmp_path)  # second instance = restart
    assert b.is_trusted((tmp_path / ".dashboard-secret").read_text().strip())
    assert a._secret == b._secret


def test_secret_regenerated_if_deleted(tmp_path):
    a = make(tmp_path)
    old = a._secret
    (tmp_path / ".dashboard-secret").unlink()
    b = make(tmp_path)
    assert b._secret != old  # all devices revoked
    assert not b.is_trusted(old)


def test_empty_secret_file_is_replaced(tmp_path):
    (tmp_path / ".dashboard-secret").write_text("")
    a = make(tmp_path)
    assert a._secret


def test_mint_redeem_round_trip(tmp_path):
    a = make(tmp_path)
    token = a.mint_token()
    assert a.redeem(token) == a._secret


def test_token_is_single_use(tmp_path):
    a = make(tmp_path)
    token = a.mint_token()
    assert a.redeem(token) is not None
    assert a.redeem(token) is None


def test_expired_token_rejected(tmp_path):
    a = make(tmp_path)
    token = a.mint_token(now=1000.0)
    assert a.redeem(token, now=1000.0 + TOKEN_TTL_SECONDS + 1) is None


def test_unknown_token_rejected(tmp_path):
    assert make(tmp_path).redeem("never-minted") is None


def test_expired_tokens_purged_on_mint(tmp_path):
    a = make(tmp_path)
    a.mint_token(now=1000.0)
    a.mint_token(now=1000.0 + TOKEN_TTL_SECONDS + 1)
    assert len(a._tokens) == 1


def test_is_trusted(tmp_path):
    a = make(tmp_path)
    assert a.is_trusted(a._secret) is True
    assert a.is_trusted("wrong-value") is False
    assert a.is_trusted("") is False
    assert a.is_trusted(None) is False
