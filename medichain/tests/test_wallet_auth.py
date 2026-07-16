"""Wallet authentication, replay protection, sessions, and role tests."""

from pathlib import Path
import sys
import tempfile
import time

import jwt
import pytest
from eth_account import Account
from eth_account.messages import encode_defunct


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from auth_store import AuthStore  # noqa: E402
from wallet_auth import (  # noqa: E402
    AuthenticationError,
    AuthorizationError,
    WalletAuthService,
)


JWT_SECRET = "wallet-auth-test-secret-" + ("x" * 64)


@pytest.fixture()
def auth():
    with tempfile.TemporaryDirectory() as tmp:
        store = AuthStore(f"sqlite:///{tmp}/auth.db")
        store.initialize()
        account = Account.create()
        regulator = Account.create()
        admin = Account.create()
        service = WalletAuthService(
            store=store,
            jwt_secret=JWT_SECRET,
            jwt_issuer="medichain-test",
            jwt_audience="medichain-web-test",
            domain="app.example.test",
            uri="https://app.example.test",
            chain_id=4221,
            challenge_ttl_seconds=300,
            session_ttl_seconds=3600,
            regulator_wallets=[regulator.address],
            admin_wallets=[admin.address],
        )
        yield service, store, account, regulator, admin


def login(service, account):
    challenge = service.issue_challenge(account.address, 4221)
    signature = Account.sign_message(
        encode_defunct(text=challenge["message"]),
        account.key,
    ).signature.hex()
    return service.verify_challenge(
        challenge["challenge_id"],
        account.address,
        signature,
    ), challenge, signature


def test_challenge_is_bound_to_bradbury_and_wallet(auth):
    service, _store, account, _regulator, _admin = auth
    challenge = service.issue_challenge(account.address, 4221)
    assert f"{account.address.lower()}" in challenge["message"]
    assert "Chain ID: 4221" in challenge["message"]
    assert "will not trigger a blockchain transaction or cost gas" in challenge["message"]
    with pytest.raises(AuthenticationError, match="chain 4221"):
        service.issue_challenge(account.address, 1)


def test_valid_signature_issues_recoverable_session(auth):
    service, _store, account, _regulator, _admin = auth
    result, _challenge, _signature = login(service, account)
    principal = service.authenticate(result["access_token"])
    assert principal.address == account.address.lower()
    assert principal.role == "sponsor"
    assert result["token_type"] == "bearer"


def test_signature_from_different_wallet_is_rejected(auth):
    service, _store, account, regulator, _admin = auth
    challenge = service.issue_challenge(account.address, 4221)
    signature = Account.sign_message(
        encode_defunct(text=challenge["message"]),
        regulator.key,
    ).signature.hex()
    with pytest.raises(AuthenticationError, match="does not match"):
        service.verify_challenge(challenge["challenge_id"], account.address, signature)


def test_challenge_cannot_be_replayed(auth):
    service, _store, account, _regulator, _admin = auth
    _result, challenge, signature = login(service, account)
    with pytest.raises(AuthenticationError, match="already been used"):
        service.verify_challenge(challenge["challenge_id"], account.address, signature)


def test_expired_challenge_is_rejected(auth, monkeypatch):
    service, _store, account, _regulator, _admin = auth
    challenge = service.issue_challenge(account.address, 4221)
    signature = Account.sign_message(
        encode_defunct(text=challenge["message"]),
        account.key,
    ).signature.hex()
    monkeypatch.setattr(time, "time", lambda: challenge["expires_at"] + 1)
    with pytest.raises(AuthenticationError, match="expired"):
        service.verify_challenge(challenge["challenge_id"], account.address, signature)


def test_logout_revokes_session_before_jwt_expiry(auth):
    service, _store, account, _regulator, _admin = auth
    result, _challenge, _signature = login(service, account)
    principal = service.authenticate(result["access_token"])
    service.logout(principal)
    with pytest.raises(AuthenticationError, match="no longer active"):
        service.authenticate(result["access_token"])


def test_tampered_jwt_is_rejected(auth):
    service, _store, account, _regulator, _admin = auth
    result, _challenge, _signature = login(service, account)
    token = result["access_token"]
    replacement = "a" if token[-1] != "a" else "b"
    with pytest.raises(AuthenticationError, match="invalid"):
        service.authenticate(token[:-1] + replacement)


def test_wrong_jwt_audience_is_rejected(auth):
    service, _store, account, _regulator, _admin = auth
    result, _challenge, _signature = login(service, account)
    claims = jwt.decode(
        result["access_token"],
        JWT_SECRET,
        algorithms=["HS256"],
        audience="medichain-web-test",
        issuer="medichain-test",
    )
    claims["aud"] = "another-app"
    invalid = jwt.encode(claims, JWT_SECRET, algorithm="HS256")
    with pytest.raises(AuthenticationError, match="invalid"):
        service.authenticate(invalid)


def test_privileged_wallets_receive_configured_roles(auth):
    service, _store, _account, regulator, admin = auth
    regulator_result, _challenge, _signature = login(service, regulator)
    admin_result, _challenge, _signature = login(service, admin)
    assert regulator_result["user"]["role"] == "regulator"
    assert admin_result["user"]["role"] == "admin"


def test_role_authorization_is_enforced(auth):
    service, _store, account, regulator, _admin = auth
    sponsor_result, _challenge, _signature = login(service, account)
    regulator_result, _challenge, _signature = login(service, regulator)
    sponsor = service.authenticate(sponsor_result["access_token"])
    regulator_principal = service.authenticate(regulator_result["access_token"])

    service.require_roles(sponsor, ["sponsor", "admin"])
    service.require_roles(regulator_principal, ["regulator", "admin"])
    with pytest.raises(AuthorizationError, match="not permitted"):
        service.require_roles(sponsor, ["regulator", "admin"])
