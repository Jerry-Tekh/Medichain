"""
End-to-end integration test for MediChain.

This test suite verifies two separate things, both required for a real
"frontend and backend are interacting the way they're meant to" claim:

  A) FUNCTIONAL: the full user journey through the real HTTP/JSON stack
     (FastAPI's TestClient exercises actual routing, pydantic validation,
     and JSON serialization -- not just calling Python functions directly).
     This runs the Theranos fraud scenario and the legitimate-DSMB-amendment
     scenario end to end, through register -> submit_results -> dashboard
     view -> appeal -> whistleblower flag.

  B) CONTRACT AGREEMENT: a static check that every endpoint path and every
     JSON field name the frontend (frontend/app.js) actually sends/expects
     matches the backend's real pydantic models and routes -- so a typo or
     drift between the two would fail this test, not surface later as a
     runtime bug in the browser.

Run: pytest -v   (from the tests/ directory, or point pytest at this file)

NOTE ON TEST ORDERING: this file shares one module-level FastAPI `app` /
`contract` instance across all tests (imported once from backend/main.py),
the same way the real deployed backend would have one running instance.
A few tests deliberately build on state left behind by earlier tests in
this file (e.g. re-using the THERANOS-001 / CARDIO-204 trials that
test_full_journey_* already registered, rather than re-registering them).
pytest preserves file-order by default and no order-randomizing plugin is
configured here, so this is safe as written -- but it does mean this
suite is not safe to run with pytest-randomly or in parallel workers
without further isolation work.
"""

import json
import os
import re
import sys
import ast
import atexit
import shutil
import tempfile

import pytest
from fastapi.testclient import TestClient

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "contract"))

TEST_STATE_DIR = tempfile.mkdtemp(prefix="medichain-integration-")
atexit.register(shutil.rmtree, TEST_STATE_DIR, True)
os.environ["MEDICHAIN_ENV"] = "test"
os.environ["MEDICHAIN_BACKEND_MODE"] = "local"
os.environ["MEDICHAIN_STATE_PATH"] = os.path.join(TEST_STATE_DIR, "state.json")

from main import app, contract, settings  # noqa: E402
from main import RegisterTrialRequest, SubmitResultsRequest, SubmitFlagRequest, ResolveAppealRequest  # noqa: E402
from medichain_contract import IntegrityCheckError  # noqa: E402

client = TestClient(app)

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
CONTRACT_DIR = os.path.join(os.path.dirname(__file__), "..", "contract")
GENLAYER_ADAPTER = os.path.join(CONTRACT_DIR, "genlayer_adapter.py")


# =========================================================================
# A) FUNCTIONAL END-TO-END TESTS
# =========================================================================

def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_readiness():
    r = client.get("/api/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


def test_full_journey_fraud_case():
    """Theranos-style outcome-switching case: register -> submit -> flagged -> appeal."""

    # 1. Register
    r = client.post("/api/register_trial", json={
        "trial_id": "THERANOS-001",
        "clinicaltrials_gov_url": "https://clinicaltrials.gov/study/THERANOS-001",
        "primary_hypothesis": "Edison device is non-inferior to standard lab methods",
        "primary_endpoints": ["diagnostic concordance vs reference lab"],
        "expected_sample_size": 200,
        "sponsor_wallet": "0x1111111111111111111111111111111111111111",
        "integrity_bond": 5000,
    })
    assert r.status_code == 200, r.text
    trial = r.json()
    assert trial["status"] == "active"
    assert trial["bond_status"] == "held"
    assert trial["sponsor"] == "0x0000000000000000000000000000000000000001"
    assert "diagnostic concordance" in trial["protocol_snapshot"].lower()

    # 2. Submit results -- the published paper reports a completely
    # different endpoint (a satisfaction survey) on a much smaller sample
    # size than what was pre-registered, with no explanation. (Note: this
    # specifically exercises the paper-vs-protocol mismatch signal, not
    # registry-drift-over-time -- see mock_fetcher.py's module docstring
    # for why the mock can't safely simulate the latter.)
    r = client.post("/api/submit_results", json={
        "trial_id": "THERANOS-001",
        "report_id": "report-theranos-1",
        "publication_url": "https://journal.example.org/theranos-outcomes-2016",
    })
    assert r.status_code == 200, r.text
    report = r.json()

    assert report["verdict"] == "suspected_fraud"
    assert report["confidence"] == "high"
    assert report["endpoints_match"] is False
    flag_types = {f["type"] for f in report["flags"]}
    assert "outcome_switching" in flag_types
    critical_flags = [f for f in report["flags"] if f["severity"] == "critical"]
    assert len(critical_flags) >= 1

    # 3. Rule 7 check: bond must NOT be slashed in the same transaction.
    # Trial should be "flagged" with an open appeal window, bond still "held".
    r = client.get("/api/trial/THERANOS-001")
    assert r.status_code == 200
    trial_after = r.json()
    assert trial_after["status"] == "flagged"
    assert trial_after["appeal_window_open"] is True
    assert trial_after["bond_status"] == "held", (
        "Bond was slashed inside submit_results -- this violates rule 7 "
        "(suspected_fraud must never auto-slash in the same transaction)."
    )

    # 4. Whistleblower flag submission on the same trial
    r = client.post("/api/submit_flag", json={
        "trial_id": "THERANOS-001",
        "submitter": "anonymous-lab-tech",
        "description": "I personally saw the endpoint get changed after the interim data came in.",
    })
    assert r.status_code == 200, r.text
    flag = r.json()
    assert flag["trial_id"] == "THERANOS-001"
    assert flag["status"] == "open"
    assert flag["submitter"] == "0x0000000000000000000000000000000000000001"

    r = client.get("/api/trial/THERANOS-001/flags")
    assert r.status_code == 200
    flags = r.json()
    assert len(flags) == 1

    # 5. Resolve the appeal (separate call, as required) -- confirm fraud
    r = client.post("/api/resolve_appeal", json={
        "trial_id": "THERANOS-001",
        "decision": "confirm_fraud",
        "resolver": "regulator-council-1",
    })
    assert r.status_code == 200, r.text
    resolved = r.json()
    assert resolved["status"] == "resolved_fraud"
    assert resolved["bond_status"] == "slashed"
    assert resolved["appeal_window_open"] is False
    assert resolved["resolved_by"] == "0x0000000000000000000000000000000000000001"


def test_full_journey_legitimate_amendment_not_flagged_as_fraud():
    """
    This is the core design claim from the spec: a documented DSMB
    early-stopping decision must NOT trigger a fraud flag just because the
    sample size differs from what was planned.
    """
    r = client.post("/api/register_trial", json={
        "trial_id": "CARDIO-204",
        "clinicaltrials_gov_url": "https://clinicaltrials.gov/study/CARDIO-204",
        "primary_hypothesis": "Drug X reduces 24-month all-cause mortality vs placebo",
        "primary_endpoints": ["overall survival at 24 months"],
        "expected_sample_size": 2000,
        "sponsor_wallet": "0x2222222222222222222222222222222222222222",
        "integrity_bond": 10000,
    })
    assert r.status_code == 200, r.text

    r = client.post("/api/submit_results", json={
        "trial_id": "CARDIO-204",
        "report_id": "report-cardio-1",
        "publication_url": "https://journal.example.org/cardio-204-results-2025",
    })
    assert r.status_code == 200, r.text
    report = r.json()

    assert report["verdict"] != "suspected_fraud", (
        "Legitimate DSMB-documented early stopping was wrongly flagged as fraud."
    )
    critical_flags = [f for f in report["flags"] if f["severity"] == "critical"]
    assert len(critical_flags) == 0
    assert report["endpoints_match"] is True

    r = client.get("/api/trial/CARDIO-204")
    trial_after = r.json()
    assert trial_after["status"] == "active", "Trial should not be flagged for a legitimate amendment."
    assert trial_after["bond_status"] == "held"


def test_unknown_trial_errors_cleanly():
    r = client.post("/api/submit_results", json={
        "trial_id": "DOES-NOT-EXIST",
        "report_id": "report-x",
        "publication_url": "https://journal.example.org/theranos-outcomes-2016",
    })
    assert r.status_code == 400
    assert "unknown trial_id" in r.json()["detail"]


def test_dashboard_listing_reflects_all_registered_trials():
    r = client.get("/api/trials")
    assert r.status_code == 200
    trials = r.json()
    assert "THERANOS-001" in trials
    assert "CARDIO-204" in trials


def test_reports_per_trial_endpoint_exposes_flags_for_dashboard():
    """
    Spec requires an 'Integrity dashboard (score + flags per trial)'.
    This was missing entirely in the first pass -- there was no way to
    list a trial's reports (and therefore its flags) at all.
    """
    r = client.get("/api/trial/THERANOS-001/reports")
    assert r.status_code == 200
    reports = r.json()
    assert len(reports) == 1
    report = list(reports.values())[0]
    assert report["trial_id"] == "THERANOS-001"
    flag_types = {f["type"] for f in report["flags"]}
    assert "outcome_switching" in flag_types


def test_reports_for_unknown_trial_is_404():
    r = client.get("/api/trial/DOES-NOT-EXIST/reports")
    assert r.status_code == 404


def test_flags_for_unknown_trial_is_404():
    """
    BUG FIX regression test: list_flags_for_trial had no existence check
    at all, unlike every other per-trial lookup (get_trial, get_report,
    list_reports_for_trial). GET /api/trial/DOES-NOT-EXIST/flags used to
    silently return 200 {} instead of 404 -- confirmed by direct testing
    before the fix, where /flags returned 200 and the structurally
    identical /reports endpoint correctly returned 404 for the exact same
    nonexistent trial_id.
    """
    r = client.get("/api/trial/DOES-NOT-EXIST/flags")
    assert r.status_code == 404


# =========================================================================
# BUG-FIX REGRESSION TESTS
# =========================================================================

def test_duplicate_trial_registration_rejected():
    payload = {
        "trial_id": "DUPLICATE-001",
        "clinicaltrials_gov_url": "https://clinicaltrials.gov/study/CARDIO-204",
        "primary_hypothesis": "x", "primary_endpoints": ["y"],
        "expected_sample_size": 100, "sponsor_wallet": "0x3333333333333333333333333333333333333333", "integrity_bond": 100,
    }
    r1 = client.post("/api/register_trial", json=payload)
    assert r1.status_code == 200
    r2 = client.post("/api/register_trial", json=payload)
    assert r2.status_code == 400
    assert "already registered" in r2.json()["detail"]


def test_duplicate_report_id_rejected():
    """
    BUG FIX regression test: originally, submitting the same report_id
    twice silently overwrote the earlier integrity report with no error.
    """
    client.post("/api/register_trial", json={
        "trial_id": "DUPREPORT-001",
        "clinicaltrials_gov_url": "https://clinicaltrials.gov/study/CARDIO-204",
        "primary_hypothesis": "x", "primary_endpoints": ["y"],
        "expected_sample_size": 100, "sponsor_wallet": "0x3333333333333333333333333333333333333333", "integrity_bond": 100,
    })
    payload = {
        "trial_id": "DUPREPORT-001",
        "report_id": "same-report-id",
        "publication_url": "https://journal.example.org/cardio-204-results-2025",
    }
    r1 = client.post("/api/submit_results", json=payload)
    assert r1.status_code == 200
    r2 = client.post("/api/submit_results", json=payload)
    assert r2.status_code == 400
    assert "already exists" in r2.json()["detail"]


def test_invalid_decision_value_rejected_by_schema():
    """decision is now a Literal type -- garbage values are rejected at the
    API boundary (422) rather than reaching contract logic."""
    r = client.post("/api/resolve_appeal", json={
        "trial_id": "THERANOS-001", "decision": "make_it_go_away", "resolver": "x",
    })
    assert r.status_code == 422


def test_resolve_appeal_when_no_appeal_open_is_rejected():
    # CARDIO-204 was never flagged (clean verdict), so no appeal is open.
    r = client.post("/api/resolve_appeal", json={
        "trial_id": "CARDIO-204", "decision": "dismiss", "resolver": "someone",
    })
    assert r.status_code == 400
    assert "no open appeal" in r.json()["detail"]


def test_resolve_appeal_twice_fails_second_time():
    client.post("/api/register_trial", json={
        "trial_id": "DOUBLE-APPEAL-001",
        "clinicaltrials_gov_url": "https://clinicaltrials.gov/study/THERANOS-001",
        "primary_hypothesis": "x", "primary_endpoints": ["diagnostic concordance"],
        "expected_sample_size": 200, "sponsor_wallet": "0x3333333333333333333333333333333333333333", "integrity_bond": 100,
    })
    client.post("/api/submit_results", json={
        "trial_id": "DOUBLE-APPEAL-001", "report_id": "double-appeal-report",
        "publication_url": "https://journal.example.org/theranos-outcomes-2016",
    })
    r1 = client.post("/api/resolve_appeal", json={
        "trial_id": "DOUBLE-APPEAL-001", "decision": "dismiss", "resolver": "regulator",
    })
    assert r1.status_code == 200
    r2 = client.post("/api/resolve_appeal", json={
        "trial_id": "DOUBLE-APPEAL-001", "decision": "confirm_fraud", "resolver": "regulator",
    })
    assert r2.status_code == 400, "Appeal window should already be closed after first resolution"


def test_cannot_submit_results_after_trial_already_resolved():
    """
    BUG FIX regression test. Found by direct reproduction: register a
    trial, get it flagged as suspected_fraud, confirm the fraud (bond
    slashed, status resolved_fraud) -- then submit ANOTHER results report
    against the SAME trial_id. Before the fix, this silently flipped
    status back to "flagged" and reopened appeal_window_open, even though
    the bond had already been permanently slashed. If a regulator then
    dismissed that reopened appeal, bond_status would have flipped to
    "released" despite having already been "slashed" -- a real
    state-consistency bug with financial-integrity consequences.
    """
    client.post("/api/register_trial", json={
        "trial_id": "TERMINAL-STATE-001",
        "clinicaltrials_gov_url": "https://clinicaltrials.gov/study/THERANOS-001",
        "primary_hypothesis": "x", "primary_endpoints": ["diagnostic concordance"],
        "expected_sample_size": 200, "sponsor_wallet": "0x3333333333333333333333333333333333333333", "integrity_bond": 100,
    })
    client.post("/api/submit_results", json={
        "trial_id": "TERMINAL-STATE-001", "report_id": "terminal-state-report-1",
        "publication_url": "https://journal.example.org/theranos-outcomes-2016",
    })
    r_appeal = client.post("/api/resolve_appeal", json={
        "trial_id": "TERMINAL-STATE-001", "decision": "confirm_fraud", "resolver": "regulator",
    })
    assert r_appeal.status_code == 200
    assert r_appeal.json()["bond_status"] == "slashed"

    # Now try to submit a second report against the same, already-resolved trial.
    r_second = client.post("/api/submit_results", json={
        "trial_id": "TERMINAL-STATE-001", "report_id": "terminal-state-report-2",
        "publication_url": "https://journal.example.org/theranos-outcomes-2016",
    })
    assert r_second.status_code == 400
    assert "already been resolved" in r_second.json()["detail"]

    # And confirm the trial's bond state is untouched by the rejected attempt.
    r_trial = client.get("/api/trial/TERMINAL-STATE-001")
    assert r_trial.json()["bond_status"] == "slashed"
    assert r_trial.json()["status"] == "resolved_fraud"
    assert r_trial.json()["appeal_window_open"] is False


def test_empty_publication_url_rejected():
    client.post("/api/register_trial", json={
        "trial_id": "EMPTYPUB-001",
        "clinicaltrials_gov_url": "https://clinicaltrials.gov/study/CARDIO-204",
        "primary_hypothesis": "x", "primary_endpoints": ["y"],
        "expected_sample_size": 100, "sponsor_wallet": "0x3333333333333333333333333333333333333333", "integrity_bond": 100,
    })
    r = client.post("/api/submit_results", json={
        "trial_id": "EMPTYPUB-001", "report_id": "emptypub-report", "publication_url": "",
    })
    assert r.status_code == 422


def test_empty_whistleblower_submitter_or_description_rejected():
    client.post("/api/register_trial", json={
        "trial_id": "EMPTYFLAG-001",
        "clinicaltrials_gov_url": "https://clinicaltrials.gov/study/CARDIO-204",
        "primary_hypothesis": "x", "primary_endpoints": ["y"],
        "expected_sample_size": 100, "sponsor_wallet": "0x3333333333333333333333333333333333333333", "integrity_bond": 100,
    })
    r1 = client.post("/api/submit_flag", json={
        "trial_id": "EMPTYFLAG-001", "submitter": "", "description": "something happened",
    })
    assert r1.status_code == 422

    r2 = client.post("/api/submit_flag", json={
        "trial_id": "EMPTYFLAG-001", "submitter": "someone", "description": "",
    })
    assert r2.status_code == 422


def test_whistleblower_flag_on_unknown_trial_rejected():
    r = client.post("/api/submit_flag", json={
        "trial_id": "GHOST-TRIAL", "submitter": "x", "description": "y",
    })
    assert r.status_code == 400
    assert "unknown trial_id" in r.json()["detail"]


def test_get_report_unknown_id_is_404():
    r = client.get("/api/report/does-not-exist")
    assert r.status_code == 404


def test_negative_sample_size_rejected():
    r = client.post("/api/register_trial", json={
        "trial_id": "BADSIZE-001",
        "clinicaltrials_gov_url": "https://clinicaltrials.gov/study/CARDIO-204",
        "primary_hypothesis": "x", "primary_endpoints": ["y"],
        "expected_sample_size": -5, "sponsor_wallet": "0x3333333333333333333333333333333333333333", "integrity_bond": 100,
    })
    assert r.status_code == 422


def test_zero_bond_rejected():
    r = client.post("/api/register_trial", json={
        "trial_id": "BADBOND-001",
        "clinicaltrials_gov_url": "https://clinicaltrials.gov/study/CARDIO-204",
        "primary_hypothesis": "x", "primary_endpoints": ["y"],
        "expected_sample_size": 100, "sponsor_wallet": "0x3333333333333333333333333333333333333333", "integrity_bond": 0,
    })
    assert r.status_code == 422


def test_empty_trial_id_rejected():
    r = client.post("/api/register_trial", json={
        "trial_id": "",
        "clinicaltrials_gov_url": "https://clinicaltrials.gov/study/CARDIO-204",
        "primary_hypothesis": "x", "primary_endpoints": ["y"],
        "expected_sample_size": 100, "sponsor_wallet": "0x3333333333333333333333333333333333333333", "integrity_bond": 100,
    })
    assert r.status_code == 422


def test_cors_headers_present_for_cross_origin_frontend():
    """
    The frontend and backend run on different ports (different origins in
    browser terms). Without correct CORS headers, the browser would block
    every fetch() call from index.html even though curl/pytest (same-process
    HTTP clients) would appear to work fine. This test simulates a real
    cross-origin browser request.
    """
    r = client.options(
        "/api/register_trial",
        headers={
            "Origin": "http://127.0.0.1:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "http://127.0.0.1:3000"

    r2 = client.get("/api/health", headers={"Origin": "http://127.0.0.1:3000"})
    assert r2.status_code == 200
    assert r2.headers.get("access-control-allow-origin") == "http://127.0.0.1:3000"


def test_development_cors_is_restricted_to_known_local_origins():
    assert "*" not in settings.allowed_origins
    assert "http://127.0.0.1:3000" in settings.allowed_origins


# =========================================================================
# B) FRONTEND <-> BACKEND CONTRACT AGREEMENT (static cross-check)
# =========================================================================

def test_genlayer_adapter_is_bradbury_schema_ready():
    """
    Regression guard for the Bradbury deploy failure:
    "cannot get contract schema" was caused by using a network-rejected
    runner alias and schema-hostile storage declarations. Bradbury's
    current deployed runner exposes gl.Contract, not gl.contract.
    """
    source = open(GENLAYER_ADAPTER, encoding="utf-8").read()
    first_line = source.splitlines()[0]
    pinned_runner = (
        '# { "Depends": '
        '"py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }'
    )

    assert first_line == pinned_runner
    assert "py-genlayer:test" not in source
    assert "py-genlayer:latest" not in source
    assert "class MediChain(gl.Contract)" in source
    assert "@gl.contract" not in source
    assert "emit_raw_event" not in source
    assert "= TreeMap()" not in source
    assert "self.owner = gl.message.sender_account" in source
    assert "only the MediChain relayer can perform writes" in source

    tree = ast.parse(source)
    medichain_class = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "MediChain"
    )
    annotations = [
        ast.unparse(stmt.annotation)
        for stmt in medichain_class.body
        if isinstance(stmt, ast.AnnAssign)
    ]

    assert "TreeMap[str, dict]" not in annotations
    assert "TreeMap[str, list]" not in annotations
    assert "TreeMap[str, int]" not in annotations
    assert "Address" in annotations
    assert "TreeMap[str, u256]" in annotations
    assert "TreeMap[str, bigint]" in annotations

    init = next(
        node for node in medichain_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    init_annotations = {
        arg.arg: ast.unparse(arg.annotation)
        for arg in init.args.args
        if arg.annotation is not None
    }
    assert init_annotations["treasury_address"] == "Address"

    for stmt in init.body:
        if not isinstance(stmt, ast.Assign) or not isinstance(stmt.value, ast.Call):
            continue
        func = stmt.value.func
        is_treemap_constructor = (
            isinstance(func, ast.Subscript)
            and isinstance(func.value, ast.Name)
            and func.value.id == "TreeMap"
        )
        assert not is_treemap_constructor, (
            "Bradbury initializes annotated TreeMap storage; do not assign TreeMap[...]() in __init__"
        )

    register_trial = next(
        node for node in medichain_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "register_trial"
    )
    arg_annotations = {
        arg.arg: ast.unparse(arg.annotation)
        for arg in register_trial.args.args
        if arg.annotation is not None
    }
    assert arg_annotations["sponsor_wallet"] == "str"
    assert arg_annotations["integrity_bond"] == "u256"

def _read_frontend_js():
    with open(os.path.join(FRONTEND_DIR, "app.js"), encoding="utf-8") as f:
        return f.read()


def test_frontend_calls_only_real_backend_routes():
    js = _read_frontend_js()
    called_paths = set(re.findall(r'callApi\(\s*[`"\']([^`"\']+)', js))
    # normalize template-literal paths like `/api/trial/${...}/flags`
    normalized = set()
    for p in called_paths:
        normalized.add(re.sub(r"\$\{[^}]*\}", "{trial_id}", p))

    backend_routes = {r.path for r in app.routes if hasattr(r, "path")}

    for path in normalized:
        # match either an exact route or a parameterized one
        matches = any(
            path == route or
            re.fullmatch(re.escape(route).replace(r"\{trial_id\}", r"[^/]+").replace(r"\{report_id\}", r"[^/]+"), path)
            for route in backend_routes
        )
        assert matches, f"Frontend calls {path!r} but no matching backend route exists: {backend_routes}"


def test_frontend_api_base_is_runtime_configured():
    html = open(os.path.join(FRONTEND_DIR, "index.html"), encoding="utf-8").read()
    js = _read_frontend_js()
    config_js = open(os.path.join(FRONTEND_DIR, "config.js"), encoding="utf-8").read()

    assert 'value="http://localhost:8000"' not in html
    assert '<script src="config.js"></script>' in html
    assert "MEDICHAIN_CONFIG" in config_js
    assert "MEDICHAIN_CONFIG" in js
    assert "Authorization" in js


def test_register_form_fields_match_pydantic_model():
    js = _read_frontend_js()
    # extract the field names the register form sends (from formToJson + explicit payload fields)
    form_html = open(os.path.join(FRONTEND_DIR, "index.html"), encoding="utf-8").read()
    register_section = form_html.split('id="registerForm"')[1].split("</form>")[0]
    frontend_fields = set(re.findall(r'name="([a-zA-Z_]+)"', register_section))

    backend_fields = set(RegisterTrialRequest.model_fields.keys())

    assert frontend_fields == backend_fields, (
        f"Register form fields {frontend_fields} do not match backend model fields {backend_fields}"
    )


def test_submit_results_form_fields_match_pydantic_model():
    form_html = open(os.path.join(FRONTEND_DIR, "index.html"), encoding="utf-8").read()
    section = form_html.split('id="submitForm"')[1].split("</form>")[0]
    frontend_fields = set(re.findall(r'name="([a-zA-Z_]+)"', section))
    backend_fields = set(SubmitResultsRequest.model_fields.keys())
    assert frontend_fields == backend_fields


def test_flag_form_fields_match_pydantic_model():
    form_html = open(os.path.join(FRONTEND_DIR, "index.html"), encoding="utf-8").read()
    section = form_html.split('id="flagForm"')[1].split("</form>")[0]
    frontend_fields = set(re.findall(r'name="([a-zA-Z_]+)"', section))
    backend_fields = set(SubmitFlagRequest.model_fields.keys())
    assert frontend_fields == backend_fields


def test_appeal_form_fields_match_pydantic_model():
    form_html = open(os.path.join(FRONTEND_DIR, "index.html"), encoding="utf-8").read()
    section = form_html.split('id="appealForm"')[1].split("</form>")[0]
    frontend_fields = set(re.findall(r'name="([a-zA-Z_]+)"', section))
    backend_fields = set(ResolveAppealRequest.model_fields.keys())
    assert frontend_fields == backend_fields


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
