"""MediChain API backend.

Development uses a persistent local simulator. Production must be configured
for the deployed GenLayer Bradbury contract with restricted CORS and write
authentication.
"""

import os
import ipaddress
import logging
import secrets
import sys
from typing import Annotated, List, Literal, Optional
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field, field_validator

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "contract"))
from config import load_settings  # noqa: E402
from genlayer_client import GenLayerCliGateway, GenLayerGatewayError  # noqa: E402
from medichain_contract import IntegrityCheckError  # noqa: E402
from mock_fetcher import mock_webpage_fetcher  # noqa: E402
from mock_llm import mock_llm_client  # noqa: E402
from persistence import PersistentMediChainContract  # noqa: E402

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


app = FastAPI(
    title="MediChain API",
    version="1.0.0",
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.allowed_origins),
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
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


def require_write_auth(
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> None:
    if not settings.require_write_auth:
        return

    token = x_api_key or _bearer_token(authorization)
    if not token:
        raise HTTPException(401, "write API token is required")
    token_matches = False
    for expected in settings.api_tokens:
        token_matches |= secrets.compare_digest(token, expected)
    if not token_matches:
        raise HTTPException(403, "invalid write API token")


ShortText = Annotated[str, Field(min_length=1, max_length=256)]
Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]
ExternalUrl = Annotated[str, Field(min_length=9, max_length=2048)]
WalletAddress = Annotated[str, Field(pattern=r"^0x[0-9a-fA-F]{40}$")]


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
    sponsor_wallet: WalletAddress
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
    resolver: ShortText


class SubmitFlagRequest(BaseModel):
    trial_id: Identifier
    submitter: ShortText
    description: Annotated[str, Field(min_length=1, max_length=5000)]
    evidence_url: Annotated[Optional[str], Field(max_length=2048)] = ""

    @field_validator("evidence_url")
    @classmethod
    def validate_evidence_url(cls, value: Optional[str]) -> Optional[str]:
        return validate_external_https_url(value) if value else value


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "environment": settings.environment,
        "backend_mode": settings.backend_mode,
        "contract_address": settings.genlayer_contract_address or None,
    }


@app.get("/api/ready")
def ready():
    try:
        if settings.backend_mode == "genlayer":
            treasury = contract.call("get_treasury_address")
            if not treasury:
                raise IntegrityCheckError("Bradbury treasury read returned no value")
        else:
            contract.list_trials()
    except (IntegrityCheckError, GenLayerGatewayError) as exc:
        logger.warning("Contract readiness check failed: %s", exc)
        raise HTTPException(503, "contract gateway unavailable") from exc
    return {
        "status": "ready",
        "backend_mode": settings.backend_mode,
        "contract_address": settings.genlayer_contract_address or None,
    }


@app.post("/api/register_trial")
def register_trial(req: RegisterTrialRequest, _: None = Depends(require_write_auth)):
    try:
        return contract.register_trial(**req.model_dump())
    except (IntegrityCheckError, GenLayerGatewayError) as exc:
        raise _contract_http_error(exc, 400) from exc


@app.post("/api/submit_results")
def submit_results(req: SubmitResultsRequest, _: None = Depends(require_write_auth)):
    try:
        return contract.submit_results(**req.model_dump())
    except (IntegrityCheckError, GenLayerGatewayError) as exc:
        raise _contract_http_error(exc, 400) from exc


@app.post("/api/resolve_appeal")
def resolve_appeal(req: ResolveAppealRequest, _: None = Depends(require_write_auth)):
    try:
        return contract.resolve_appeal(**req.model_dump())
    except (IntegrityCheckError, GenLayerGatewayError) as exc:
        raise _contract_http_error(exc, 400) from exc


@app.post("/api/submit_flag")
def submit_flag(req: SubmitFlagRequest, _: None = Depends(require_write_auth)):
    try:
        flag_id, flag = contract.submit_flag(**req.model_dump())
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
