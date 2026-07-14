"""
MediChain local backend (FastAPI).

This is NOT the GenLayer deployment. It's a local simulation server that
runs the exact same contract logic (contract/medichain_contract.py) behind
a REST API, so the frontend can be built and tested against real
request/response cycles before anything touches GenLayer Studio.

Run: uvicorn main:app --reload --port 8000   (from the backend/ directory)
"""

import os
import sys
from typing import List, Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "contract"))
from medichain_contract import MediChainContract, IntegrityCheckError  # noqa: E402
from mock_fetcher import mock_webpage_fetcher  # noqa: E402
from mock_llm import mock_llm_client  # noqa: E402

app = FastAPI(title="MediChain API (local simulation)")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

contract = MediChainContract(mock_webpage_fetcher, mock_llm_client)


class RegisterTrialRequest(BaseModel):
    trial_id: str = Field(min_length=1)
    clinicaltrials_gov_url: str
    primary_hypothesis: str
    primary_endpoints: List[str]
    expected_sample_size: int = Field(gt=0)
    sponsor_wallet: str
    integrity_bond: int = Field(gt=0)


class SubmitResultsRequest(BaseModel):
    trial_id: str
    report_id: str
    publication_url: str
    preprint_url: Optional[str] = ""


class ResolveAppealRequest(BaseModel):
    trial_id: str
    decision: Literal["confirm_fraud", "dismiss"]
    resolver: str


class SubmitFlagRequest(BaseModel):
    trial_id: str
    submitter: str
    description: str
    evidence_url: Optional[str] = ""


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/register_trial")
def register_trial(req: RegisterTrialRequest):
    try:
        return contract.register_trial(**req.model_dump())
    except IntegrityCheckError as e:
        raise HTTPException(400, str(e))


@app.post("/api/submit_results")
def submit_results(req: SubmitResultsRequest):
    try:
        return contract.submit_results(**req.model_dump())
    except IntegrityCheckError as e:
        raise HTTPException(400, str(e))


@app.post("/api/resolve_appeal")
def resolve_appeal(req: ResolveAppealRequest):
    try:
        return contract.resolve_appeal(**req.model_dump())
    except IntegrityCheckError as e:
        raise HTTPException(400, str(e))


@app.post("/api/submit_flag")
def submit_flag(req: SubmitFlagRequest):
    try:
        flag_id, flag = contract.submit_flag(**req.model_dump())
        return flag
    except IntegrityCheckError as e:
        raise HTTPException(400, str(e))


@app.get("/api/trial/{trial_id}")
def get_trial(trial_id: str):
    try:
        return contract.get_trial(trial_id)
    except IntegrityCheckError as e:
        raise HTTPException(404, str(e))


@app.get("/api/report/{report_id}")
def get_report(report_id: str):
    try:
        return contract.get_report(report_id)
    except IntegrityCheckError as e:
        raise HTTPException(404, str(e))


@app.get("/api/trials")
def list_trials():
    return contract.list_trials()


@app.get("/api/trial/{trial_id}/flags")
def list_flags(trial_id: str):
    # BUG FIX: this endpoint previously returned 200 {} for an unknown
    # trial_id instead of 404, inconsistent with every other per-trial
    # endpoint. Now that the contract method itself validates existence,
    # this needs the same try/except as /reports.
    try:
        return contract.list_flags_for_trial(trial_id)
    except IntegrityCheckError as e:
        raise HTTPException(404, str(e))


@app.get("/api/trial/{trial_id}/reports")
def list_reports(trial_id: str):
    # MISSING FEATURE (now added): the spec requires an "Integrity
    # dashboard (score + flags per trial)". Previously there was no way
    # to list all integrity reports -- and therefore all flags -- for a
    # given trial; the dashboard could only ever show the latest score.
    try:
        return contract.list_reports_for_trial(trial_id)
    except IntegrityCheckError as e:
        raise HTTPException(404, str(e))
