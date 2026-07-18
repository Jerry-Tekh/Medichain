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
- independent LLM validation with deterministic decision-field comparison
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
    object_start = cleaned.find("{")
    object_end = cleaned.rfind("}")
    if object_start >= 0 and object_end >= object_start:
        cleaned = cleaned[object_start:object_end + 1]
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


def _clinicaltrials_api_url(source_url: str) -> str:
    normalized = source_url.upper()
    marker_index = normalized.find("NCT")
    if marker_index < 0:
        _fail("[EXPECTED] ClinicalTrials.gov URL must contain an NCT identifier")
    registry_id = normalized[marker_index:marker_index + 11]
    if (
        len(registry_id) != 11
        or not registry_id.startswith("NCT")
        or not registry_id[3:].isdigit()
    ):
        _fail("[EXPECTED] ClinicalTrials.gov URL contains an invalid NCT identifier")
    return f"https://clinicaltrials.gov/api/v2/studies/{registry_id}"


def _validate_protocol_snapshot(raw_json: str, source_url: str) -> str:
    try:
        data = json.loads(_clean_json_response(raw_json))
    except Exception:
        _fail("[EXPECTED] protocol snapshot must be valid JSON")
        return ""
    if not isinstance(data, dict):
        _fail("[EXPECTED] protocol snapshot must be a JSON object")

    expected_registry_id = _clinicaltrials_api_url(source_url).rsplit("/", 1)[-1]
    registry_id = str(data.get("registry_id", "")).strip().upper()
    official_title = str(data.get("official_title", "")).strip()
    if registry_id != expected_registry_id:
        _fail("[EXPECTED] protocol snapshot NCT identifier does not match source URL")
    if not official_title:
        _fail("[EXPECTED] protocol snapshot has no study title")

    enrollment = _coerce_int(data.get("enrollment", 0) or 0, "enrollment")
    if enrollment < 0:
        _fail("[EXPECTED] protocol snapshot enrollment must not be negative")

    primary_outcomes = data.get("primary_outcomes", [])
    if not isinstance(primary_outcomes, list):
        _fail("[EXPECTED] protocol snapshot primary outcomes must be a list")
    outcomes = []
    for item in primary_outcomes:
        text = str(item).strip()
        if text:
            outcomes.append(text[:500])
    if not outcomes:
        _fail("[EXPECTED] protocol snapshot has no primary outcomes")

    snapshot = {
        "registry_id": registry_id[:128],
        "official_title": official_title[:1000],
        "overall_status": str(data.get("overall_status", "unknown")).strip()[:128],
        "enrollment": enrollment,
        "primary_outcomes": outcomes[:20],
        "summary": str(data.get("summary", "")).strip()[:1500],
        "source_url": source_url,
    }
    return json.dumps(snapshot, sort_keys=True)


def _validate_document_snapshot(
    raw_json: str,
    source_url: str,
    field_name: str,
) -> str:
    try:
        data = json.loads(_clean_json_response(raw_json))
    except Exception:
        _fail(f"[EXPECTED] {field_name} snapshot must be valid JSON")
        return ""
    if not isinstance(data, dict):
        _fail(f"[EXPECTED] {field_name} snapshot must be a JSON object")
    if str(data.get("source_url", "")).strip() != source_url:
        _fail(f"[EXPECTED] {field_name} snapshot URL does not match submission")
    text = str(data.get("text", "")).strip()
    if not text:
        _fail(f"[EXPECTED] {field_name} snapshot contains no readable text")
    return text[:8000]


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


def _parse_integrity_result(raw_json) -> dict:
    if isinstance(raw_json, dict):
        data = raw_json
    else:
        try:
            data = json.loads(_clean_json_response(raw_json))
        except Exception:
            _fail("[LLM_ERROR] integrity analysis returned invalid JSON")
            return {}
    if not isinstance(data, dict):
        _fail("[LLM_ERROR] integrity analysis must return a JSON object")

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


def _critical_flag_types(result: dict) -> list[str]:
    critical_types = []
    for flag in result["flags"]:
        if (
            flag["severity"] == "critical"
            and flag["type"] not in critical_types
        ):
            critical_types.append(flag["type"])
    critical_types.sort()
    return critical_types


def _integrity_results_equivalent(leader: dict, validator: dict) -> bool:
    if leader["overall_verdict"] != validator["overall_verdict"]:
        return False
    if abs(leader["integrity_score"] - validator["integrity_score"]) > 10:
        return False
    if leader["endpoints_match"] != validator["endpoints_match"]:
        return False
    if (
        leader["sample_size_consistent"]
        != validator["sample_size_consistent"]
    ):
        return False

    leader_actionable_fraud = (
        leader["overall_verdict"] == "suspected_fraud"
        and leader["confidence"] == "high"
    )
    validator_actionable_fraud = (
        validator["overall_verdict"] == "suspected_fraud"
        and validator["confidence"] == "high"
    )
    if leader_actionable_fraud != validator_actionable_fraud:
        return False

    return _critical_flag_types(leader) == _critical_flag_types(validator)


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
        self.owner = gl.message.sender_address
        self.treasury_address = treasury_address
        self.trial_ids_json = "[]"

    def _require_owner(self) -> None:
        if gl.message.sender_address != self.owner:
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
        protocol_snapshot_json: str,
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
        protocol_snapshot = _validate_protocol_snapshot(
            protocol_snapshot_json,
            clinicaltrials_gov_url,
        )

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
        preprint_url: str,
        current_registry_snapshot_json: str,
        publication_snapshot_json: str,
        preprint_snapshot_json: str,
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
        current_registry = _validate_protocol_snapshot(
            current_registry_snapshot_json,
            registry_url,
        )
        paper = _validate_document_snapshot(
            publication_snapshot_json,
            publication_url,
            "publication",
        )
        preprint_text = ""
        if preprint_url:
            preprint_text = _validate_document_snapshot(
                preprint_snapshot_json,
                preprint_url,
                "preprint",
            )
        elif preprint_snapshot_json.strip():
            _fail("[EXPECTED] preprint snapshot requires a preprint URL")

        def run_integrity_analysis() -> dict:
            prompt = _build_integrity_prompt(
                protocol_snapshot,
                current_registry,
                hypothesis,
                endpoints_json,
                expected_n,
                paper,
                preprint_text,
            )
            response = gl.nondet.exec_prompt(
                prompt,
                response_format="json",
            )
            if not isinstance(response, dict):
                _fail("[LLM_ERROR] integrity analysis must return a JSON object")
            return _parse_integrity_result(response)

        def validate_integrity_analysis(leader_result: gl.vm.Result) -> bool:
            if not isinstance(leader_result, gl.vm.Return):
                return False
            try:
                leader = _parse_integrity_result(leader_result.calldata)
                validator = run_integrity_analysis()
            except Exception:
                return False
            return _integrity_results_equivalent(leader, validator)

        result = gl.vm.run_nondet_unsafe(
            run_integrity_analysis,
            validate_integrity_analysis,
        )
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
