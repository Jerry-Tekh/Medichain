# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""
GenLayer Bradbury deployment adapter for MediChain.

This file is intentionally self-contained for single-file deployment. It
mirrors the local MediChain rules while using GenLayer-safe storage and
consensus patterns:

- pinned runner dependency in the first line for Bradbury/testnet deploys
- gl.Contract class declaration for the current Bradbury runner
- TreeMap storage with primitive values instead of dict/list state
- JSON strings for report/flag arrays returned by view methods
- comparative validation for LLM/web analysis instead of strict equality
"""

from genlayer import *
import json


FLAG_TYPES = [
    "outcome_switching",
    "sample_size_discrepancy",
    "p_hacking",
    "undisclosed_amendment",
    "other",
]
FLAG_SEVERITIES = ["critical", "moderate", "minor"]
VERDICTS = ["clean", "concerns", "suspected_fraud"]
CONFIDENCE_LEVELS = ["high", "medium", "low"]

EQUIVALENCE_PRINCIPLE = """
Two integrity assessments of the same clinical trial submission are
equivalent if ALL of the following hold:
- overall_verdict falls in the same category: clean, concerns, or suspected_fraud
- integrity_score is within +/-10 points
- endpoints_match has the same boolean value
- every critical severity flag present in one assessment is also present
  in the other assessment with the same type value

Minor differences in flag wording, description text, summary text, or
non-critical flags do not need to match.
"""


def _fail(message: str) -> None:
    # The pinned runner lacks a dedicated user-error class; a built-in
    # exception preserves the intended failure and message.
    raise Exception(message)


def _clean_json_response(raw: str) -> str:
    cleaned = str(raw).strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    return cleaned


def _load_json_list(raw: str) -> list:
    if raw == "":
        return []
    return json.loads(raw)


def _append_json_id(raw: str, item_id: str) -> str:
    items = _load_json_list(raw)
    items.append(item_id)
    return json.dumps(items)


def _coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    lowered = str(value).strip().lower()
    if lowered in ("true", "yes", "1"):
        return True
    if lowered in ("false", "no", "0"):
        return False
    _fail("LLM response contained an invalid boolean")
    return False


def _coerce_int(value, field_name: str) -> int:
    try:
        return int(value)
    except Exception:
        _fail(f"LLM response field {field_name} must be an integer")
    return 0


def _clean_flags(value) -> list[dict]:
    if not isinstance(value, list):
        _fail("LLM response flags must be a list")

    cleaned = []
    for item in value:
        if not isinstance(item, dict):
            _fail("Each LLM flag must be an object")
        flag_type = str(item.get("type", "other")).strip()
        if flag_type not in FLAG_TYPES:
            flag_type = "other"
        severity = str(item.get("severity", "minor")).strip()
        if severity not in FLAG_SEVERITIES:
            severity = "minor"
        cleaned.append({
            "type": flag_type,
            "severity": severity,
            "description": str(item.get("description", ""))[:500],
        })
    return cleaned


def _parse_integrity_result(raw_json: str) -> dict:
    try:
        data = json.loads(_clean_json_response(raw_json))
    except Exception:
        _fail("[LLM_ERROR] integrity analysis returned invalid JSON")
        return {}

    score = _coerce_int(data.get("integrity_score", -1), "integrity_score")
    if score < 0 or score > 100:
        _fail("LLM response integrity_score must be between 0 and 100")

    verdict = str(data.get("overall_verdict", "")).strip()
    if verdict not in VERDICTS:
        _fail("LLM response overall_verdict is invalid")

    confidence = str(data.get("confidence", "")).strip()
    if confidence not in CONFIDENCE_LEVELS:
        _fail("LLM response confidence is invalid")

    return {
        "integrity_score": score,
        "flags": _clean_flags(data.get("flags", [])),
        "endpoints_match": _coerce_bool(data.get("endpoints_match", False)),
        "sample_size_consistent": _coerce_bool(data.get("sample_size_consistent", False)),
        "overall_verdict": verdict,
        "confidence": confidence,
        "summary": str(data.get("summary", ""))[:1000],
    }


def _build_integrity_prompt(
    protocol_snapshot: str,
    current_registry: str,
    hypothesis: str,
    endpoints_json: str,
    expected_n: int,
    paper: str,
    preprint_text: str,
) -> str:
    endpoints = _load_json_list(endpoints_json)
    endpoints_text = ", ".join([str(endpoint) for endpoint in endpoints])
    return f"""
You are a clinical trial data integrity specialist. Assess whether this published result is consistent with the pre-registered protocol.

PRE-REGISTERED PROTOCOL (snapshot at registration):
{protocol_snapshot[:2000]}

CURRENT REGISTRY STATE (may show amendments):
{current_registry[:2000]}

PRE-REGISTERED HYPOTHESIS: {hypothesis}
PRE-REGISTERED PRIMARY ENDPOINTS: {endpoints_text}
EXPECTED SAMPLE SIZE: {expected_n}

PUBLISHED PAPER:
{paper[:3000]}

PREPRINT (if available):
{preprint_text[:2000]}

Check for:
1. Outcome switching
2. Sample size inconsistencies, unless explained by a documented DSMB early stop
3. Post-hoc subgroup analyses presented as primary
4. Implausible p-values or effect sizes
5. Undisclosed protocol amendments

Respond only with JSON:
{{
  "integrity_score": 0,
  "flags": [
    {{"type": "outcome_switching", "severity": "critical", "description": "one sentence"}}
  ],
  "endpoints_match": true,
  "sample_size_consistent": true,
  "overall_verdict": "clean",
  "confidence": "high",
  "summary": "2-3 sentence assessment"
}}
""".strip()


class MediChain(gl.Contract):
    owner: Address
    treasury_address: Address
    trial_ids_json: str
    trial_exists: TreeMap[str, bool]
    trial_sponsor: TreeMap[str, str]
    trial_registry_url: TreeMap[str, str]
    trial_protocol_snapshot: TreeMap[str, str]
    trial_hypothesis: TreeMap[str, str]
    trial_endpoints_json: TreeMap[str, str]
    trial_expected_n: TreeMap[str, bigint]
    trial_bond: TreeMap[str, u256]
    trial_bond_status: TreeMap[str, str]
    trial_status: TreeMap[str, str]
    trial_integrity_score: TreeMap[str, bigint]
    trial_latest_verdict: TreeMap[str, str]
    trial_appeal_window_open: TreeMap[str, bool]
    trial_reports_json: TreeMap[str, str]
    trial_flags_json: TreeMap[str, str]
    trial_resolved_by: TreeMap[str, str]

    report_exists: TreeMap[str, bool]
    report_trial_id: TreeMap[str, str]
    report_publication_url: TreeMap[str, str]
    report_integrity_score: TreeMap[str, bigint]
    report_flags_json: TreeMap[str, str]
    report_endpoints_match: TreeMap[str, bool]
    report_sample_size_consistent: TreeMap[str, bool]
    report_verdict: TreeMap[str, str]
    report_confidence: TreeMap[str, str]
    report_summary: TreeMap[str, str]

    flag_exists: TreeMap[str, bool]
    flag_trial_id: TreeMap[str, str]
    flag_submitter: TreeMap[str, str]
    flag_description: TreeMap[str, str]
    flag_evidence_url: TreeMap[str, str]
    flag_status: TreeMap[str, str]

    def __init__(self, treasury_address: Address):
        self.owner = gl.message.sender_account
        self.treasury_address = treasury_address
        self.trial_ids_json = "[]"

    def _require_owner(self) -> None:
        if gl.message.sender_account != self.owner:
            _fail("only the MediChain relayer can perform writes")

    def _require_trial(self, trial_id: str) -> None:
        if not self.trial_exists.get(trial_id, False):
            _fail(f"unknown trial_id '{trial_id}'")

    def _trial_as_dict(self, trial_id: str) -> dict:
        score = self.trial_integrity_score[trial_id]
        latest_verdict = self.trial_latest_verdict[trial_id]
        return {
            "trial_id": trial_id,
            "sponsor": self.trial_sponsor[trial_id],
            "registry_url": self.trial_registry_url[trial_id],
            "protocol_snapshot": self.trial_protocol_snapshot[trial_id],
            "hypothesis": self.trial_hypothesis[trial_id],
            "endpoints": _load_json_list(self.trial_endpoints_json[trial_id]),
            "expected_n": self.trial_expected_n[trial_id],
            "bond": self.trial_bond[trial_id],
            "bond_status": self.trial_bond_status[trial_id],
            "status": self.trial_status[trial_id],
            "integrity_score": None if score < 0 else score,
            "latest_verdict": None if latest_verdict == "" else latest_verdict,
            "appeal_window_open": self.trial_appeal_window_open[trial_id],
            "resolved_by": self.trial_resolved_by[trial_id],
            "treasury_address": self.treasury_address,
        }

    def _report_as_dict(self, report_id: str) -> dict:
        return {
            "report_id": report_id,
            "trial_id": self.report_trial_id[report_id],
            "publication_url": self.report_publication_url[report_id],
            "integrity_score": self.report_integrity_score[report_id],
            "flags": _load_json_list(self.report_flags_json[report_id]),
            "endpoints_match": self.report_endpoints_match[report_id],
            "sample_size_consistent": self.report_sample_size_consistent[report_id],
            "verdict": self.report_verdict[report_id],
            "confidence": self.report_confidence[report_id],
            "summary": self.report_summary[report_id],
        }

    def _flag_as_dict(self, flag_id: str) -> dict:
        return {
            "flag_id": flag_id,
            "trial_id": self.flag_trial_id[flag_id],
            "submitter": self.flag_submitter[flag_id],
            "description": self.flag_description[flag_id],
            "evidence_url": self.flag_evidence_url[flag_id],
            "status": self.flag_status[flag_id],
        }

    @gl.public.write
    def register_trial(
        self,
        trial_id: str,
        clinicaltrials_gov_url: str,
        primary_hypothesis: str,
        primary_endpoints: list[str],
        expected_sample_size: int,
        sponsor_wallet: str,
        integrity_bond: u256,
    ) -> None:
        self._require_owner()
        if not trial_id or not trial_id.strip():
            _fail("trial_id must not be empty")
        if self.trial_exists.get(trial_id, False):
            _fail(f"trial_id '{trial_id}' already registered")
        if not clinicaltrials_gov_url or not clinicaltrials_gov_url.strip():
            _fail("clinicaltrials_gov_url must not be empty")
        if not primary_endpoints:
            _fail("primary_endpoints must not be empty")
        if expected_sample_size <= 0:
            _fail("expected_sample_size must be positive")
        if integrity_bond <= 0:
            _fail("integrity_bond must be positive")

        def fetch_protocol() -> str:
            return gl.get_webpage(clinicaltrials_gov_url, mode="text")

        protocol_snapshot = gl.eq_principle.strict_eq(fetch_protocol)

        self.trial_exists[trial_id] = True
        self.trial_sponsor[trial_id] = sponsor_wallet
        self.trial_registry_url[trial_id] = clinicaltrials_gov_url
        self.trial_protocol_snapshot[trial_id] = protocol_snapshot[:5000]
        self.trial_hypothesis[trial_id] = primary_hypothesis
        self.trial_endpoints_json[trial_id] = json.dumps(primary_endpoints)
        self.trial_expected_n[trial_id] = expected_sample_size
        self.trial_bond[trial_id] = integrity_bond
        self.trial_bond_status[trial_id] = "held"
        self.trial_status[trial_id] = "active"
        self.trial_integrity_score[trial_id] = -1
        self.trial_latest_verdict[trial_id] = ""
        self.trial_appeal_window_open[trial_id] = False
        self.trial_reports_json[trial_id] = "[]"
        self.trial_flags_json[trial_id] = "[]"
        self.trial_resolved_by[trial_id] = ""
        self.trial_ids_json = _append_json_id(self.trial_ids_json, trial_id)

    @gl.public.write
    def submit_results(
        self,
        trial_id: str,
        report_id: str,
        publication_url: str,
        preprint_url: str = "",
    ) -> None:
        self._require_owner()
        self._require_trial(trial_id)
        if not report_id or not report_id.strip():
            _fail("report_id must not be empty")
        if self.report_exists.get(report_id, False):
            _fail(f"report_id '{report_id}' already exists")
        if self.trial_status[trial_id] in ("resolved_fraud", "resolved_clean"):
            _fail(f"trial '{trial_id}' has already been resolved")
        if not publication_url or not publication_url.strip():
            _fail("publication_url must not be empty")

        registry_url = self.trial_registry_url[trial_id]
        protocol_snapshot = self.trial_protocol_snapshot[trial_id]
        hypothesis = self.trial_hypothesis[trial_id]
        endpoints_json = self.trial_endpoints_json[trial_id]
        expected_n = self.trial_expected_n[trial_id]

        def run_integrity_analysis() -> str:
            current_registry = gl.get_webpage(registry_url, mode="text")
            paper = gl.get_webpage(publication_url, mode="text")
            preprint_text = ""
            if preprint_url:
                preprint_text = gl.get_webpage(preprint_url, mode="text")
            prompt = _build_integrity_prompt(
                protocol_snapshot,
                current_registry,
                hypothesis,
                endpoints_json,
                expected_n,
                paper,
                preprint_text,
            )
            return gl.nondet.exec_prompt(prompt)

        raw_json = gl.eq_principle.prompt_comparative(run_integrity_analysis, EQUIVALENCE_PRINCIPLE)
        result = _parse_integrity_result(raw_json)
        flags_json = json.dumps(result["flags"])

        self.report_exists[report_id] = True
        self.report_trial_id[report_id] = trial_id
        self.report_publication_url[report_id] = publication_url
        self.report_integrity_score[report_id] = result["integrity_score"]
        self.report_flags_json[report_id] = flags_json
        self.report_endpoints_match[report_id] = result["endpoints_match"]
        self.report_sample_size_consistent[report_id] = result["sample_size_consistent"]
        self.report_verdict[report_id] = result["overall_verdict"]
        self.report_confidence[report_id] = result["confidence"]
        self.report_summary[report_id] = result["summary"]
        self.trial_reports_json[trial_id] = _append_json_id(self.trial_reports_json[trial_id], report_id)

        self.trial_integrity_score[trial_id] = result["integrity_score"]
        self.trial_latest_verdict[trial_id] = result["overall_verdict"]

        if result["overall_verdict"] == "suspected_fraud" and result["confidence"] == "high":
            self.trial_status[trial_id] = "flagged"
            self.trial_appeal_window_open[trial_id] = True

    @gl.public.write
    def resolve_appeal(self, trial_id: str, decision: str, resolver: str) -> None:
        self._require_owner()
        self._require_trial(trial_id)
        if self.trial_status[trial_id] != "flagged" or not self.trial_appeal_window_open[trial_id]:
            _fail("no open appeal for this trial")
        if decision not in ("confirm_fraud", "dismiss"):
            _fail("decision must be 'confirm_fraud' or 'dismiss'")
        if not resolver or not resolver.strip():
            _fail("resolver must not be empty")

        if decision == "confirm_fraud":
            self.trial_bond_status[trial_id] = "slashed"
            self.trial_status[trial_id] = "resolved_fraud"
        else:
            self.trial_bond_status[trial_id] = "released"
            self.trial_status[trial_id] = "resolved_clean"

        self.trial_appeal_window_open[trial_id] = False
        self.trial_resolved_by[trial_id] = resolver

    @gl.public.write
    def submit_flag(self, trial_id: str, submitter: str, description: str, evidence_url: str = "") -> str:
        self._require_owner()
        self._require_trial(trial_id)
        if not submitter or not submitter.strip():
            _fail("submitter must not be empty")
        if not description or not description.strip():
            _fail("description must not be empty")

        flag_id = f"{trial_id}-{len(_load_json_list(self.trial_flags_json[trial_id]))}"
        self.flag_exists[flag_id] = True
        self.flag_trial_id[flag_id] = trial_id
        self.flag_submitter[flag_id] = submitter
        self.flag_description[flag_id] = description
        self.flag_evidence_url[flag_id] = evidence_url
        self.flag_status[flag_id] = "open"
        self.trial_flags_json[trial_id] = _append_json_id(self.trial_flags_json[trial_id], flag_id)
        return flag_id

    @gl.public.view
    def get_trial(self, trial_id: str) -> dict:
        self._require_trial(trial_id)
        return self._trial_as_dict(trial_id)

    @gl.public.view
    def get_report(self, report_id: str) -> dict:
        if not self.report_exists.get(report_id, False):
            _fail(f"unknown report_id '{report_id}'")
        return self._report_as_dict(report_id)

    @gl.public.view
    def get_treasury_address(self) -> Address:
        return self.treasury_address

    @gl.public.view
    def get_owner(self) -> Address:
        return self.owner

    @gl.public.view
    def list_trials(self) -> dict:
        trials = {}
        for trial_id in _load_json_list(self.trial_ids_json):
            trials[trial_id] = self._trial_as_dict(trial_id)
        return trials

    @gl.public.view
    def list_reports_for_trial(self, trial_id: str) -> dict:
        self._require_trial(trial_id)
        reports = {}
        for report_id in _load_json_list(self.trial_reports_json[trial_id]):
            reports[report_id] = self._report_as_dict(report_id)
        return reports

    @gl.public.view
    def list_flags_for_trial(self, trial_id: str) -> dict:
        self._require_trial(trial_id)
        flags = {}
        for flag_id in _load_json_list(self.trial_flags_json[trial_id]):
            flags[flag_id] = self._flag_as_dict(flag_id)
        return flags
