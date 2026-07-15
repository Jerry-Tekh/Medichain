"""Runtime configuration for the MediChain API."""

from dataclasses import dataclass
import json
import os
import re
from urllib.parse import urlparse


DEFAULT_LOCAL_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
)
DEFAULT_LOCAL_HOSTS = ("localhost", "127.0.0.1", "testserver")
BRADBURY_NETWORK = "testnet-bradbury"
ADDRESS_PATTERN = re.compile(r"^0x[0-9a-fA-F]{40}$")
PRIVATE_KEY_PATTERN = re.compile(r"^(?:0x)?[0-9a-fA-F]{64}$")
ACCOUNT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
JWT_SECRET_MIN_LENGTH = 64


def _csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _origins(value: str) -> tuple[str, ...]:
    return tuple(origin.rstrip("/") for origin in _csv(value))


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be a boolean")


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


@dataclass(frozen=True)
class Settings:
    environment: str
    backend_mode: str
    allowed_origins: tuple[str, ...]
    allowed_hosts: tuple[str, ...]
    require_write_auth: bool
    api_tokens: tuple[str, ...]
    wallet_auth_required: bool
    auth_database_url: str
    jwt_secret: str
    jwt_issuer: str
    jwt_audience: str
    auth_domain: str
    auth_uri: str
    auth_chain_id: int
    auth_challenge_ttl_seconds: int
    auth_session_ttl_seconds: int
    regulator_wallets: tuple[str, ...]
    admin_wallets: tuple[str, ...]
    state_path: str
    genlayer_contract_address: str
    genlayer_rpc_url: str
    genlayer_network: str
    genlayer_account_name: str
    genlayer_private_key: str
    genlayer_cli_command: str
    genlayer_cli_fees: str
    genlayer_keystore_password: str
    genlayer_timeout_seconds: int

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def validate(self) -> None:
        if self.environment not in {"development", "test", "production"}:
            raise RuntimeError("MEDICHAIN_ENV must be development, test, or production")
        if self.backend_mode not in {"local", "genlayer"}:
            raise RuntimeError("MEDICHAIN_BACKEND_MODE must be 'local' or 'genlayer'")

        if not self.allowed_origins:
            raise RuntimeError("ALLOWED_ORIGINS must include at least one origin")
        for origin in self.allowed_origins:
            if origin == "*":
                continue
            parsed = urlparse(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.path not in {"", "/"}
                or parsed.params
                or parsed.query
                or parsed.fragment
                or parsed.username
                or parsed.password
            ):
                raise RuntimeError(f"invalid CORS origin: {origin}")
            if self.is_production and parsed.scheme != "https":
                raise RuntimeError("ALLOWED_ORIGINS must use HTTPS in production")
        if self.is_production and "*" in self.allowed_origins:
            raise RuntimeError("ALLOWED_ORIGINS must not contain '*' in production")
        if self.is_production:
            local_origins = [
                origin for origin in self.allowed_origins
                if "localhost" in origin or "127.0.0.1" in origin
            ]
            if local_origins:
                raise RuntimeError("ALLOWED_ORIGINS must not use localhost origins in production")

        if not self.allowed_hosts:
            raise RuntimeError("ALLOWED_HOSTS must include at least one host")
        for host in self.allowed_hosts:
            wildcard = host.startswith("*.")
            bare_host = host[2:] if wildcard else host
            if (
                not bare_host
                or "*" in bare_host
                or ":" in bare_host
                or "://" in host
                or "/" in host
                or any(character.isspace() for character in host)
            ):
                raise RuntimeError(f"invalid allowed host: {host}")
        if self.is_production and "*" in self.allowed_hosts:
            raise RuntimeError("ALLOWED_HOSTS must not contain '*' in production")
        if self.is_production and any(
            host in {"localhost", "127.0.0.1"} for host in self.allowed_hosts
        ):
            raise RuntimeError("ALLOWED_HOSTS must not use localhost in production")

        if self.is_production and self.backend_mode != "genlayer":
            raise RuntimeError("production requires MEDICHAIN_BACKEND_MODE=genlayer")

        if self.is_production and not self.wallet_auth_required:
            raise RuntimeError("production requires MEDICHAIN_WALLET_AUTH_REQUIRED=true")
        if self.is_production and len(self.jwt_secret) < JWT_SECRET_MIN_LENGTH:
            raise RuntimeError(
                f"JWT_SECRET must contain at least {JWT_SECRET_MIN_LENGTH} characters"
            )
        if not self.jwt_issuer.strip() or not self.jwt_audience.strip():
            raise RuntimeError("JWT_ISSUER and JWT_AUDIENCE must not be empty")
        if self.is_production:
            if not self.auth_database_url.strip():
                raise RuntimeError("DATABASE_URL is required in production")
            if not self.auth_database_url.startswith(("postgres://", "postgresql://")):
                raise RuntimeError("production DATABASE_URL must use PostgreSQL")
            if not self.auth_domain.strip() or not self.auth_uri.strip():
                raise RuntimeError("MEDICHAIN_AUTH_DOMAIN and MEDICHAIN_AUTH_URI are required")
            if not self.auth_uri.startswith("https://"):
                raise RuntimeError("MEDICHAIN_AUTH_URI must use HTTPS in production")
        if not 60 <= self.auth_challenge_ttl_seconds <= 900:
            raise RuntimeError("MEDICHAIN_AUTH_CHALLENGE_TTL_SECONDS must be between 60 and 900")
        if not 300 <= self.auth_session_ttl_seconds <= 86_400:
            raise RuntimeError("MEDICHAIN_AUTH_SESSION_TTL_SECONDS must be between 300 and 86400")
        if self.auth_chain_id <= 0:
            raise RuntimeError("MEDICHAIN_AUTH_CHAIN_ID must be a positive integer")
        for wallet in self.regulator_wallets + self.admin_wallets:
            if not ADDRESS_PATTERN.fullmatch(wallet):
                raise RuntimeError(f"invalid privileged wallet address: {wallet}")

        if self.backend_mode == "local":
            if not self.state_path.strip():
                raise RuntimeError("MEDICHAIN_STATE_PATH is required for local mode")
            return

        if not ADDRESS_PATTERN.fullmatch(self.genlayer_contract_address):
            raise RuntimeError("MEDICHAIN_CONTRACT_ADDRESS must be a 20-byte hex address")
        if not self.genlayer_rpc_url:
            raise RuntimeError("GENLAYER_RPC_URL is required for genlayer mode")
        rpc_url = urlparse(self.genlayer_rpc_url)
        if (
            rpc_url.scheme not in {"http", "https"}
            or not rpc_url.netloc
            or rpc_url.username
            or rpc_url.password
            or rpc_url.query
            or rpc_url.fragment
        ):
            raise RuntimeError("GENLAYER_RPC_URL must be an HTTP(S) URL without credentials")
        if self.is_production and rpc_url.scheme != "https":
            raise RuntimeError("GENLAYER_RPC_URL must use HTTPS in production")
        if self.is_production and self.genlayer_network != BRADBURY_NETWORK:
            raise RuntimeError(f"production GENLAYER_NETWORK must be {BRADBURY_NETWORK}")
        if not ACCOUNT_NAME_PATTERN.fullmatch(self.genlayer_account_name):
            raise RuntimeError("GENLAYER_ACCOUNT_NAME contains unsupported characters")
        if not self.genlayer_cli_command:
            raise RuntimeError("GENLAYER_CLI_COMMAND must not be empty")
        if not 30 <= self.genlayer_timeout_seconds <= 900:
            raise RuntimeError("GENLAYER_TIMEOUT_SECONDS must be between 30 and 900")
        try:
            fees = json.loads(self.genlayer_cli_fees)
        except json.JSONDecodeError as exc:
            raise RuntimeError("GENLAYER_CLI_FEES must be valid JSON") from exc
        if not isinstance(fees, dict):
            raise RuntimeError("GENLAYER_CLI_FEES must be a JSON object")

        if self.is_production:
            if not PRIVATE_KEY_PATTERN.fullmatch(self.genlayer_private_key):
                raise RuntimeError("PRIVATE_KEY must be a 32-byte hex key")
            if len(self.genlayer_keystore_password) < 8:
                raise RuntimeError("GENLAYER_KEYSTORE_PASSWORD must contain at least 8 characters")


def load_settings() -> Settings:
    environment = os.getenv("MEDICHAIN_ENV", "development").strip().lower()
    backend_mode = os.getenv("MEDICHAIN_BACKEND_MODE", "local").strip().lower()
    allowed_origins = _origins(os.getenv("ALLOWED_ORIGINS", ",".join(DEFAULT_LOCAL_ORIGINS)))
    allowed_hosts = _csv(os.getenv("ALLOWED_HOSTS", ",".join(DEFAULT_LOCAL_HOSTS)))
    api_tokens = _csv(os.getenv("API_TOKENS", ""))
    require_write_auth = _bool_env("MEDICHAIN_REQUIRE_WRITE_AUTH", environment == "production")
    wallet_auth_required = _bool_env(
        "MEDICHAIN_WALLET_AUTH_REQUIRED",
        environment == "production",
    )
    database_url = os.getenv(
        "DATABASE_URL",
        f"sqlite:///{os.getenv('MEDICHAIN_AUTH_DB_PATH', 'data/medichain_auth.db')}",
    ).strip()
    auth_uri = os.getenv(
        "MEDICHAIN_AUTH_URI",
        next((origin for origin in allowed_origins if origin.startswith("https://")), ""),
    ).strip().rstrip("/")
    auth_domain = os.getenv("MEDICHAIN_AUTH_DOMAIN", "").strip()
    if not auth_domain and auth_uri:
        auth_domain = urlparse(auth_uri).netloc
    def _wallets(name: str) -> tuple[str, ...]:
        return tuple(item.lower() for item in _csv(os.getenv(name, "")))

    settings = Settings(
        environment=environment,
        backend_mode=backend_mode,
        allowed_origins=allowed_origins,
        allowed_hosts=allowed_hosts,
        require_write_auth=require_write_auth,
        api_tokens=api_tokens,
        wallet_auth_required=wallet_auth_required,
        auth_database_url=database_url,
        jwt_secret=os.getenv(
            "JWT_SECRET",
            "development-wallet-auth-secret-" + ("x" * 64)
            if environment != "production"
            else "",
        ).strip(),
        jwt_issuer=os.getenv("JWT_ISSUER", "medichain-api").strip(),
        jwt_audience=os.getenv("JWT_AUDIENCE", "medichain-web").strip(),
        auth_domain=auth_domain,
        auth_uri=auth_uri,
        auth_chain_id=_int_env("MEDICHAIN_AUTH_CHAIN_ID", 4221),
        auth_challenge_ttl_seconds=_int_env("MEDICHAIN_AUTH_CHALLENGE_TTL_SECONDS", 300),
        auth_session_ttl_seconds=_int_env("MEDICHAIN_AUTH_SESSION_TTL_SECONDS", 3600),
        regulator_wallets=_wallets("MEDICHAIN_REGULATOR_WALLETS"),
        admin_wallets=_wallets("MEDICHAIN_ADMIN_WALLETS"),
        state_path=os.getenv("MEDICHAIN_STATE_PATH", "data/medichain_state.json"),
        genlayer_contract_address=(
            os.getenv("MEDICHAIN_CONTRACT_ADDRESS")
            or os.getenv("GENLAYER_CONTRACT_ADDRESS", "")
        ).strip(),
        genlayer_rpc_url=os.getenv("GENLAYER_RPC_URL", "").strip(),
        genlayer_network=os.getenv("GENLAYER_NETWORK", "testnet-bradbury").strip(),
        genlayer_account_name=os.getenv("GENLAYER_ACCOUNT_NAME", "medichain-production").strip(),
        genlayer_private_key=os.getenv("PRIVATE_KEY", "").strip(),
        genlayer_cli_command=os.getenv("GENLAYER_CLI_COMMAND", "genlayer").strip(),
        genlayer_cli_fees=os.getenv(
            "GENLAYER_CLI_FEES",
            '{"distribution":{"leaderTimeunitsAllocation":"1000","validatorTimeunitsAllocation":"1000","rotations":["0"]}}',
        ).strip(),
        genlayer_keystore_password=os.getenv("GENLAYER_KEYSTORE_PASSWORD", ""),
        genlayer_timeout_seconds=_int_env("GENLAYER_TIMEOUT_SECONDS", 600),
    )
    settings.validate()
    return settings
