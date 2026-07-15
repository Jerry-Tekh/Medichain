"""Wallet challenge verification and revocable JWT session management."""

from dataclasses import dataclass
from datetime import datetime, timezone
import secrets
import threading
import time
from typing import Iterable

from eth_account import Account
from eth_account.messages import encode_defunct
import jwt

from auth_store import AuthStore


SUPPORTED_ROLES = frozenset({"sponsor", "regulator", "admin"})


class AuthenticationError(Exception):
    """Raised when a wallet challenge or session is invalid."""


class AuthorizationError(Exception):
    """Raised when a valid wallet lacks the required role."""


@dataclass(frozen=True)
class WalletPrincipal:
    address: str
    role: str
    session_id: str
    expires_at: int


class AttemptLimiter:
    """Bound authentication endpoint abuse for a single API process."""

    def __init__(self, limit: int = 30, window_seconds: int = 60):
        self.limit = limit
        self.window_seconds = window_seconds
        self._attempts: dict[str, list[int]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, now: int) -> None:
        cutoff = now - self.window_seconds
        with self._lock:
            attempts = [attempt for attempt in self._attempts.get(key, []) if attempt > cutoff]
            if len(attempts) >= self.limit:
                raise AuthenticationError("too many authentication attempts; try again shortly")
            attempts.append(now)
            self._attempts[key] = attempts
            if len(self._attempts) > 5_000:
                self._attempts = {
                    item_key: item_attempts
                    for item_key, item_attempts in self._attempts.items()
                    if any(attempt > cutoff for attempt in item_attempts)
                }


class WalletAuthService:
    def __init__(
        self,
        store: AuthStore,
        jwt_secret: str,
        jwt_issuer: str,
        jwt_audience: str,
        domain: str,
        uri: str,
        chain_id: int,
        challenge_ttl_seconds: int,
        session_ttl_seconds: int,
        regulator_wallets: Iterable[str] = (),
        admin_wallets: Iterable[str] = (),
    ):
        self.store = store
        self.jwt_secret = jwt_secret
        self.jwt_issuer = jwt_issuer
        self.jwt_audience = jwt_audience
        self.domain = domain
        self.uri = uri
        self.chain_id = chain_id
        self.challenge_ttl_seconds = challenge_ttl_seconds
        self.session_ttl_seconds = session_ttl_seconds
        self.regulator_wallets = {address.lower() for address in regulator_wallets}
        self.admin_wallets = {address.lower() for address in admin_wallets}
        self.challenge_limiter = AttemptLimiter(limit=10, window_seconds=60)
        self.verify_limiter = AttemptLimiter(limit=20, window_seconds=60)

    @staticmethod
    def normalize_address(address: str) -> str:
        if not isinstance(address, str) or len(address) != 42 or not address.startswith("0x"):
            raise AuthenticationError("invalid wallet address")
        try:
            numeric = int(address[2:], 16)
        except ValueError as exc:
            raise AuthenticationError("invalid wallet address") from exc
        if numeric < 0 or numeric >= 2 ** 160:
            raise AuthenticationError("invalid wallet address")
        return "0x" + address[2:].lower()

    @staticmethod
    def _iso(timestamp: int) -> str:
        return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")

    def _role_for_new_user(self, address: str) -> str:
        if address in self.admin_wallets:
            return "admin"
        if address in self.regulator_wallets:
            return "regulator"
        return "sponsor"

    def issue_challenge(self, address: str, requested_chain_id: int) -> dict:
        normalized = self.normalize_address(address)
        now = int(time.time())
        self.challenge_limiter.check(normalized, now)
        if requested_chain_id != self.chain_id:
            raise AuthenticationError(f"wallet must be connected to chain {self.chain_id}")

        challenge_id = secrets.token_urlsafe(32)
        nonce = secrets.token_hex(16)
        expires_at = now + self.challenge_ttl_seconds
        message = (
            f"{self.domain} wants you to sign in with your wallet:\n"
            f"{normalized}\n\n"
            "Authenticate with MediChain. This request will not trigger a blockchain transaction or cost gas.\n\n"
            f"URI: {self.uri}\n"
            "Version: 1\n"
            f"Chain ID: {self.chain_id}\n"
            f"Nonce: {nonce}\n"
            f"Issued At: {self._iso(now)}\n"
            f"Expiration Time: {self._iso(expires_at)}\n"
            f"Request ID: {challenge_id}"
        )
        self.store.create_challenge(
            challenge_id=challenge_id,
            address=normalized,
            message=message,
            chain_id=self.chain_id,
            expires_at=expires_at,
            created_at=now,
        )
        return {
            "challenge_id": challenge_id,
            "message": message,
            "expires_at": expires_at,
            "chain_id": self.chain_id,
        }

    def verify_challenge(self, challenge_id: str, address: str, signature: str) -> dict:
        normalized = self.normalize_address(address)
        now = int(time.time())
        self.verify_limiter.check(normalized, now)
        challenge = self.store.get_challenge(challenge_id)
        if not challenge or challenge.address != normalized:
            raise AuthenticationError("wallet challenge was not found")
        if challenge.used_at is not None:
            raise AuthenticationError("wallet challenge has already been used")
        if challenge.expires_at < now:
            raise AuthenticationError("wallet challenge has expired")
        try:
            recovered = Account.recover_message(
                encode_defunct(text=challenge.message),
                signature=signature,
            ).lower()
        except Exception as exc:
            raise AuthenticationError("wallet signature is invalid") from exc
        if not secrets.compare_digest(recovered, normalized):
            raise AuthenticationError("wallet signature does not match the requested address")
        if not self.store.consume_challenge(challenge_id, normalized, now):
            raise AuthenticationError("wallet challenge is no longer valid")

        user = self.store.upsert_user(normalized, self._role_for_new_user(normalized), now)
        if not user.active:
            raise AuthorizationError("wallet account is disabled")
        session_id = secrets.token_urlsafe(32)
        expires_at = now + self.session_ttl_seconds
        self.store.create_session(session_id, normalized, expires_at, now)
        claims = {
            "sub": normalized,
            "role": user.role,
            "sid": session_id,
            "iss": self.jwt_issuer,
            "aud": self.jwt_audience,
            "iat": now,
            "nbf": now,
            "exp": expires_at,
            "jti": secrets.token_urlsafe(16),
        }
        token = jwt.encode(claims, self.jwt_secret, algorithm="HS256")
        return {
            "access_token": token,
            "token_type": "bearer",
            "expires_at": expires_at,
            "user": {"address": normalized, "role": user.role},
        }

    def authenticate(self, token: str) -> WalletPrincipal:
        try:
            claims = jwt.decode(
                token,
                self.jwt_secret,
                algorithms=["HS256"],
                audience=self.jwt_audience,
                issuer=self.jwt_issuer,
                options={"require": ["sub", "sid", "iat", "nbf", "exp", "iss", "aud"]},
                leeway=5,
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("wallet session has expired") from exc
        except jwt.InvalidTokenError as exc:
            raise AuthenticationError("wallet session is invalid") from exc

        address = self.normalize_address(claims.get("sub", ""))
        session_id = str(claims.get("sid", ""))
        if not session_id:
            raise AuthenticationError("wallet session is invalid")
        session = self.store.get_session(session_id)
        now = int(time.time())
        if (
            not session
            or session.address != address
            or session.revoked_at is not None
            or session.expires_at < now
        ):
            raise AuthenticationError("wallet session is no longer active")
        if not session.active:
            raise AuthorizationError("wallet account is disabled")
        if session.role not in SUPPORTED_ROLES:
            raise AuthorizationError("wallet account has an invalid role")
        return WalletPrincipal(
            address=address,
            role=session.role,
            session_id=session_id,
            expires_at=session.expires_at,
        )

    def logout(self, principal: WalletPrincipal) -> None:
        self.store.revoke_session(principal.session_id, principal.address, int(time.time()))

    @staticmethod
    def require_roles(principal: WalletPrincipal, roles: Iterable[str]) -> None:
        permitted = set(roles)
        if principal.role not in permitted:
            raise AuthorizationError("wallet role is not permitted to perform this action")
