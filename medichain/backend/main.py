"""MediChain API backend.

Development uses a persistent local simulator. Production must be configured
for the deployed GenLayer Bradbury contract with restricted CORS and write
authentication backed by wallet signatures.
"""

from contextlib import asynccontextmanager
import os
import ipaddress
import logging
import sys
from typing import Annotated, List, Literal, Optional
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, ConfigDict, Field, field_validator

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "contract"))
from auth_store import AuthStore  # noqa: E402
from config import load_settings  # noqa: E402
from genlayer_client import GenLayerCliGateway, GenLayerGatewayError  # noqa: E402
from medichain_contract import IntegrityCheckError  # noqa: E402
from mock_fetcher import mock_webpage_fetcher  # noqa: E402
from mock_llm import mock_llm_client  # noqa: E402
from persistence import PersistentMediChainContract  # noqa: E402
from wallet_auth import (  # noqa: E402
    AuthenticationError,
    AuthorizationError,
    SUPPORTED_ROLES,
    WalletAuthService,
    WalletPrincipal,
)

settings = load_settings()
logger = logging.getLogger("medichain.api")


def build_contract_gateway():
    if settings.backend_mode == "genlayer":
        return GenLayerCliGateway(
            contract_address=settings.genlayer_contract_address,
            rpc_url=settings.genlayer_rpc_url,
            network=settings.genlayer_network,
            account_name=settings.genlayer_account_name,
            private_key=settings.genlayer_private_key,
            cli_command=settings.genlayer_cli_command,
            fees=settings.genlayer_cli_fees,
            keystore_password=settings.genlayer_keystore_password,
            timeout_seconds=settings.genlayer_timeout_seconds,
        )
    return PersistentMediChainContract(
        mock_webpage_fetcher,
        mock_llm_client,
        settings.state_path,
    )


auth_store = AuthStore(settings.auth_database_url)
auth_store.initialize()
auth_service = WalletAuthService(
    store=auth_store,
    jwt_secret=settings.jwt_secret,
    jwt_issuer=settings.jwt_issuer,
    jwt_audience=settings.jwt_audience,
    domain=settings.auth_domain or "localhost",
    uri=settings.auth_uri or "http://localhost:3000",
    chain_id=settings.auth_chain_id,
    challenge_ttl_seconds=settings.auth_challenge_ttl_seconds,
    session_ttl_seconds=settings.auth_session_ttl_seconds,
    regulator_wallets=settings.regulator_wallets,
    admin_wallets=settings.admin_wallets,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    auth_store.initialize()
    yield


app = FastAPI(
    title="MediChain API",
    version="2.0.0",
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
    lifespan=lifespan,
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.allowed_origins),
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    allow_credentials=False,
    max_age=600,
)

contract = build_contract_gateway()


def _contract_http_error(exc: Exception, status_code: int) -> HTTPException:
    if isinstance(exc, GenLayerGatewayError):
        logger.warning("Bradbury contract request failed: %s", exc)
        return HTTPException(502, "Bradbury contract request failed")
    return HTTPException(status_code, str(exc))


def _bearer_token(authorization: Optional[str]) -> str:
    if not authorization:
        return ""
    scheme, separator, token = authorization.partition(" ")
    if separator and scheme.lower() == "bearer":
        return token.strip()
    return ""


def current_wallet(
    authorization: Optional[str] = Header(default=None),
) -> WalletPrincipal:
    if not settings.wallet_auth_required:
        return WalletPrincipal(
            address="0x0000000000000000000000000000000000000001",
            role="admin",
            session_id="development",
            expires_at=2 ** 31,
        )
    token = _bearer_token(authorization)
    if not token:
        raise HTTPException(401, "wallet authentication is required")
    try:
        return auth_service.authenticate(token)
    except AuthenticationError as exc:
        raise HTTPException(401, str(exc)) from exc
    except AuthorizationError as exc:
        raise HTTPException(403, str(exc)) from exc


def require_roles(*roles: str):
    def dependency(principal: WalletPrincipal = Depends(current_wallet)) -> WalletPrincipal:
        try:
            auth_service.require_roles(principal, roles)
        except AuthorizationError as exc:
            raise HTTPException(403, str(exc)) from exc
        return principal

    return dependency


ShortText = Annotated[str, Field(min_length=1, max_length=256)]
Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]
ExternalUrl = Annotated[str, Field(min_length=9, max_length=2048)]
WalletAddress = Annotated[str, Field(pattern=r"^0x[0-9a-fA-F]{40}$")]


class StrictRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def validate_external_https_url(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ValueError("must be an HTTPS URL without credentials or a fragment")
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ValueError("localhost URLs are not allowed")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address and not address.is_global:
        raise ValueError("private and non-global IP addresses are not allowed")
    return value


class RegisterTrialRequest(BaseModel):
    trial_id: Identifier
    clinicaltrials_gov_url: ExternalUrl
    primary_hypothesis: Annotated[str, Field(min_length=1, max_length=2000)]
    primary_endpoints: Annotated[List[ShortText], Field(min_length=1, max_length=20)]
    expected_sample_size: int = Field(gt=0, le=10_000_000)
    integrity_bond: int = Field(gt=0, le=(2 ** 256) - 1)

    @field_validator("clinicaltrials_gov_url")
    @classmethod
    def validate_registry_url(cls, value: str) -> str:
        validate_external_https_url(value)
        hostname = urlparse(value).hostname.lower().rstrip(".")
        if hostname not in {"clinicaltrials.gov", "www.clinicaltrials.gov"}:
            raise ValueError("must point to clinicaltrials.gov")
        return value


class SubmitResultsRequest(BaseModel):
    trial_id: Identifier
    report_id: Identifier
    publication_url: ExternalUrl
    preprint_url: Annotated[Optional[str], Field(max_length=2048)] = ""

    @field_validator("publication_url", "preprint_url")
    @classmethod
    def validate_result_urls(cls, value: Optional[str]) -> Optional[str]:
        return validate_external_https_url(value) if value else value


class ResolveAppealRequest(BaseModel):
    trial_id: Identifier
    decision: Literal["confirm_fraud", "dismiss"]


class SubmitFlagRequest(BaseModel):
    trial_id: Identifier
    description: Annotated[str, Field(min_length=1, max_length=5000)]
    evidence_url: Annotated[Optional[str], Field(max_length=2048)] = ""

    @field_validator("evidence_url")
    @classmethod
    def validate_evidence_url(cls, value: Optional[str]) -> Optional[str]:
        return validate_external_https_url(value) if value else value


class WalletChallengeRequest(StrictRequestModel):
    address: WalletAddress
    chain_id: int = Field(gt=0, le=2 ** 31)


class WalletVerifyRequest(StrictRequestModel):
    challenge_id: Annotated[
        str,
        Field(min_length=20, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"),
    ]
    address: WalletAddress
    signature: Annotated[
        str,
        Field(min_length=132, max_length=132, pattern=r"^0x[0-9a-fA-F]{130}$"),
    ]


class WalletRoleRequest(StrictRequestModel):
    role: Literal["sponsor", "regulator", "admin"]


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "environment": settings.environment,
        "backend_mode": settings.backend_mode,
        "contract_address": settings.genlayer_contract_address or None,
        "authentication": "wallet",
    }


@app.get("/api/ready")
def ready():
    owner = None
    try:
        if not auth_store.ping():
            raise RuntimeError("wallet auth database returned no result")
        if settings.backend_mode == "genlayer":
            treasury = contract.call("get_treasury_address")
            if not treasury:
                raise IntegrityCheckError("Bradbury treasury read returned no value")
            owner = contract.call("get_owner")
            if not owner:
                raise IntegrityCheckError("Bradbury owner read returned no value")
            if (
                settings.is_production
                and str(owner).lower() != contract.signer_address
            ):
                raise IntegrityCheckError(
                    "Bradbury contract owner does not match the configured relayer"
                )
        else:
            contract.list_trials()
    except (IntegrityCheckError, GenLayerGatewayError, RuntimeError) as exc:
        logger.warning("Readiness check failed: %s", exc)
        raise HTTPException(503, "application dependencies are unavailable") from exc
    return {
        "status": "ready",
        "backend_mode": settings.backend_mode,
        "contract_address": settings.genlayer_contract_address or None,
        "owner_address": str(owner).lower() if owner else None,
    }


@app.post("/api/auth/challenge")
def wallet_challenge(req: WalletChallengeRequest):
    try:
        return auth_service.issue_challenge(req.address, req.chain_id)
    except AuthenticationError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/auth/verify")
def wallet_verify(req: WalletVerifyRequest):
    try:
        return auth_service.verify_challenge(
            req.challenge_id,
            req.address,
            req.signature,
        )
    except AuthenticationError as exc:
        raise HTTPException(401, str(exc)) from exc
    except AuthorizationError as exc:
        raise HTTPException(403, str(exc)) from exc


@app.get("/api/auth/me")
def wallet_me(principal: WalletPrincipal = Depends(current_wallet)):
    return {
        "address": principal.address,
        "role": principal.role,
        "expires_at": principal.expires_at,
    }


@app.post("/api/auth/logout")
def wallet_logout(principal: WalletPrincipal = Depends(current_wallet)):
    if principal.session_id != "development":
        auth_service.logout(principal)
    return {"status": "signed_out"}


@app.put("/api/admin/users/{address}/role")
def update_wallet_role(
    address: str,
    req: WalletRoleRequest,
    _principal: WalletPrincipal = Depends(require_roles("admin")),
):
    try:
        normalized = auth_service.normalize_address(address)
    except AuthenticationError as exc:
        raise HTTPException(422, str(exc)) from exc
    if req.role not in SUPPORTED_ROLES:
        raise HTTPException(422, "unsupported wallet role")
    if not auth_store.set_user_role(normalized, req.role):
        raise HTTPException(404, "wallet user has not signed in yet")
    return {"address": normalized, "role": req.role}


@app.post("/api/register_trial")
def register_trial(
    req: RegisterTrialRequest,
    principal: WalletPrincipal = Depends(require_roles("sponsor", "admin")),
):
    try:
        return contract.register_trial(
            **req.model_dump(),
            sponsor_wallet=principal.address,
        )
    except (IntegrityCheckError, GenLayerGatewayError) as exc:
        raise _contract_http_error(exc, 400) from exc


@app.post("/api/submit_results")
def submit_results(
    req: SubmitResultsRequest,
    principal: WalletPrincipal = Depends(require_roles("sponsor", "admin")),
):
    try:
        trial = contract.get_trial(req.trial_id)
        if (
            principal.role != "admin"
            and str(trial.get("sponsor", "")).lower() != principal.address
        ):
            raise HTTPException(403, "only the trial sponsor can submit results")
        return contract.submit_results(**req.model_dump())
    except (IntegrityCheckError, GenLayerGatewayError) as exc:
        raise _contract_http_error(exc, 400) from exc


@app.post("/api/resolve_appeal")
def resolve_appeal(
    req: ResolveAppealRequest,
    principal: WalletPrincipal = Depends(require_roles("regulator", "admin")),
):
    try:
        return contract.resolve_appeal(
            **req.model_dump(),
            resolver=principal.address,
        )
    except (IntegrityCheckError, GenLayerGatewayError) as exc:
        raise _contract_http_error(exc, 400) from exc


@app.post("/api/submit_flag")
def submit_flag(
    req: SubmitFlagRequest,
    principal: WalletPrincipal = Depends(
        require_roles("sponsor", "regulator", "admin")
    ),
):
    try:
        result = contract.submit_flag(
            **req.model_dump(),
            submitter=principal.address,
        )
        if isinstance(result, tuple):
            _flag_id, flag = result
        else:
            flag = result
        return flag
    except (IntegrityCheckError, GenLayerGatewayError) as exc:
        raise _contract_http_error(exc, 400) from exc


@app.get("/api/trial/{trial_id}")
def get_trial(trial_id: str):
    try:
        return contract.get_trial(trial_id)
    except (IntegrityCheckError, GenLayerGatewayError) as exc:
        raise _contract_http_error(exc, 404) from exc


@app.get("/api/report/{report_id}")
def get_report(report_id: str):
    try:
        return contract.get_report(report_id)
    except (IntegrityCheckError, GenLayerGatewayError) as exc:
        raise _contract_http_error(exc, 404) from exc


@app.get("/api/trials")
def list_trials():
    try:
        return contract.list_trials()
    except (IntegrityCheckError, GenLayerGatewayError) as exc:
        raise _contract_http_error(exc, 400) from exc


@app.get("/api/trial/{trial_id}/flags")
def list_flags(trial_id: str):
    # BUG FIX: this endpoint previously returned 200 {} for an unknown
    # trial_id instead of 404, inconsistent with every other per-trial
    # endpoint. Now that the contract method itself validates existence,
    # this needs the same try/except as /reports.
    try:
        return contract.list_flags_for_trial(trial_id)
    except (IntegrityCheckError, GenLayerGatewayError) as exc:
        raise _contract_http_error(exc, 404) from exc


@app.get("/api/trial/{trial_id}/reports")
def list_reports(trial_id: str):
    # MISSING FEATURE (now added): the spec requires an "Integrity
    # dashboard (score + flags per trial)". Previously there was no way
    # to list all integrity reports -- and therefore all flags -- for a
    # given trial; the dashboard could only ever show the latest score.
    try:
        return contract.list_reports_for_trial(trial_id)
    except (IntegrityCheckError, GenLayerGatewayError) as exc:
        raise _contract_http_error(exc, 404) from exc
