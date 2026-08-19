"""
MediChain core contract logic.

This module implements the business rules from the MediChain spec, as a
plain Python class with INJECTED `webpage_fetcher` and `llm_client`
callables, rather than calling GenLayer's `gl.get_webpage` /
`gl.nondet.exec_prompt` directly.

Why: GenLayer's `gl` namespace only exists inside GenLayer Studio /
validator nodes, and non-deterministic calls (web fetch, LLM prompts) can
ONLY be made from inside a no-argument function passed to
`gl.eq_principle.*` -- storage is not even accessible from inside those
functions. That real control flow is quite different from "call
get_webpage, then just use the result," which is what the original spec
skeleton did. Decoupling the logic here, behind plain injected functions,
is what makes it possible to:

  1. Unit + integration test the actual business rules locally
     (see /tests/test_integration.py), with a mock fetcher/LLM standing
     in for the network calls, and
  2. Wrap this exact class's rules inside `genlayer_adapter.py`, a real
     `gl.Contract` subclass that correctly isolates each non-deterministic
     step inside `gl.eq_principle.*` closures, for real deployment.

See genlayer_adapter.py for the corrected real-SDK shape and a list of
the concrete inaccuracies found in the original spec skeleton (e.g. it
used a decorator, `@gl.contract`, and a `text_only=True` keyword argument
to `gl.get_webpage` that do not exist in the documented SDK).

This file is the single source of truth for the rules. The GenLayer
adapter and the local FastAPI backend both import it unchanged.
"""

import json
import time
import uuid


class IntegrityCheckError(Exception):
    pass


class MediChainContract:
    def __init__(self, webpage_fetcher, llm_client):
        """
        webpage_fetcher: Callable[[str], str] -> fetch a URL, return text
        llm_client:      Callable[[str], str] -> send a prompt, return raw text (expected JSON)
        """
        self._fetch = webpage_fetcher
        self._llm = llm_client
        self.trials = {}
        self.integrity_reports = {}
        self.flags = {}  # whistleblower flags, keyed by flag_id

    # ---------------- registration ----------------

    def register_trial(
        self,
        trial_id,
        clinicaltrials_gov_url,
        primary_hypothesis,
        primary_endpoints,
        expected_sample_size,
        sponsor_wallet,
        integrity_bond,
    ):
        if not trial_id or not trial_id.strip():
            raise IntegrityCheckError("trial_id must not be empty")
        if trial_id in self.trials:
            raise IntegrityCheckError(f"trial_id '{trial_id}' already registered")
        if not primary_endpoints:
            raise IntegrityCheckError("primary_endpoints must not be empty")
        if expected_sample_size <= 0:
            raise IntegrityCheckError("expected_sample_size must be positive")
        if integrity_bond <= 0:
            raise IntegrityCheckError("integrity_bond must be positive")

        # Rule 3: the protocol_snapshot is taken NOW, at registration time.
        # It is treated as immutable ground truth from this point on.
        protocol_snapshot = self._fetch(clinicaltrials_gov_url)

        self.trials[trial_id] = {
            "trial_id": trial_id,
            "sponsor": sponsor_wallet,
            "registry_url": clinicaltrials_gov_url,
            "protocol_snapshot": protocol_snapshot[:5000],
            "hypothesis": primary_hypothesis,
            "endpoints": primary_endpoints,
            "expected_n": expected_sample_size,
            "bond": integrity_bond,
            "bond_status": "held",       # held -> slashed | released
            "status": "active",          # active -> flagged -> resolved_fraud | resolved_clean
            "integrity_score": None,
            "latest_verdict": None,
            "registered_at": time.time(),
            "appeal_window_open": False,
            "appeal_deadline": None,
        }
        return dict(self.trials[trial_id])

    # ---------------- results submission + integrity analysis ----------------

    def submit_results(self, trial_id, report_id, publication_url, preprint_url=""):
        if trial_id not in self.trials:
            raise IntegrityCheckError(f"unknown trial_id '{trial_id}'")
        if report_id in self.integrity_reports:
            # BUG FIX: without this check, submitting the same report_id
            # twice silently overwrote the earlier report with no error --
            # a report_id collision (accidental or malicious) would erase
            # prior integrity history for a trial.
            raise IntegrityCheckError(f"report_id '{report_id}' already exists")
        trial = self.trials[trial_id]
        if trial["status"] in ("resolved_fraud", "resolved_clean"):
            # BUG FIX: this trial's bond has already been finally resolved
            # (slashed or released) by a prior resolve_appeal() call.
            # Without this guard, a fresh submit_results() call could still
            # produce a new suspected_fraud verdict, which would flip
            # status back to "flagged" and reopen appeal_window_open --
            # even though the bond is already gone. If a regulator then
            # dismissed that reopened appeal, bond_status would flip to
            # "released" despite having already been "slashed" (or vice
            # versa): a genuine state-consistency bug with real financial
            # consequences, confirmed by reproducing it directly:
            #   register -> submit_results (fraud) -> resolve_appeal(confirm_fraud)
            #   -> bond_status == "slashed", status == "resolved_fraud"
            #   -> submit_results() AGAIN on the same trial_id
            #   -> status silently flipped back to "flagged", appeal reopened
            # A trial whose bond has already been finally resolved is a
            # terminal state; no further results can move its bond again.
            raise IntegrityCheckError(
                f"trial '{trial_id}' has already been resolved ({trial['status']}); "
                "no further results can be submitted against its bond"
            )
        if not publication_url or not publication_url.strip():
            raise IntegrityCheckError("publication_url must not be empty")

        # Current registry state may have been amended since registration.
        current_registry = self._fetch(trial["registry_url"])
        paper = self._fetch(publication_url)
        preprint_text = self._fetch(preprint_url)[:2000] if preprint_url else ""

        prompt = self._build_prompt(trial, current_registry, paper, preprint_text)
        raw = self._llm(prompt)
        result = self._parse_llm_json(raw)
        self._validate_llm_result(result)

        report = {
            "report_id": report_id,
            "trial_id": trial_id,
            "publication_url": publication_url,
            "integrity_score": result["integrity_score"],
            "flags": result["flags"],
            "endpoints_match": result["endpoints_match"],
            "sample_size_consistent": result["sample_size_consistent"],
            "verdict": result["overall_verdict"],
            "confidence": result["confidence"],
            "summary": result["summary"],
            "submitted_at": time.time(),
        }
        self.integrity_reports[report_id] = report

        trial["integrity_score"] = result["integrity_score"]
        trial["latest_verdict"] = result["overall_verdict"]

        # Rule 7: suspected_fraud NEVER slashes the bond in this same call.
        # It only opens a human appeal window. Slashing happens (if at all)
        # in a separate resolve_appeal() call.
        if result["overall_verdict"] == "suspected_fraud" and result["confidence"] == "high":
            trial["status"] = "flagged"
            trial["appeal_window_open"] = True
            trial["appeal_deadline"] = time.time() + (14 * 24 * 3600)  # 14-day window

        return dict(report)

    # ---------------- appeal resolution (separate human-in-the-loop step) ----------------

    def resolve_appeal(self, trial_id, decision, resolver):
        """
        decision: "confirm_fraud" -> slash bond, trial marked resolved_fraud
                  "dismiss"       -> release bond, trial marked resolved_clean
        Rule 2 + Rule 7: bonds are only ever moved here, never inside
        submit_results, and only after the flagged trial reaches this
        explicit, separate resolution step.
        """
        trial = self.trials.get(trial_id)
        if not trial:
            raise IntegrityCheckError(f"unknown trial_id '{trial_id}'")
        if trial["status"] != "flagged" or not trial["appeal_window_open"]:
            raise IntegrityCheckError("no open appeal for this trial")

        if decision == "confirm_fraud":
            trial["bond_status"] = "slashed"
            trial["status"] = "resolved_fraud"
        elif decision == "dismiss":
            trial["bond_status"] = "released"
            trial["status"] = "resolved_clean"
        else:
            raise IntegrityCheckError("decision must be 'confirm_fraud' or 'dismiss'")

        trial["appeal_window_open"] = False
        trial["resolved_by"] = resolver
        trial["resolved_at"] = time.time()
        return dict(trial)

    # ---------------- whistleblower interface ----------------

    def submit_flag(self, trial_id, submitter, description, evidence_url=""):
        if trial_id not in self.trials:
            raise IntegrityCheckError(f"unknown trial_id '{trial_id}'")
        if not submitter or not submitter.strip():
            raise IntegrityCheckError("submitter must not be empty")
        if not description or not description.strip():
            raise IntegrityCheckError("description must not be empty")
        flag_id = str(uuid.uuid4())
        self.flags[flag_id] = {
            "flag_id": flag_id,
            "trial_id": trial_id,
            "submitter": submitter,
            "description": description,
            "evidence_url": evidence_url,
            "status": "open",
            "submitted_at": time.time(),
        }
        return flag_id, dict(self.flags[flag_id])

    def list_flags_for_trial(self, trial_id):
        # BUG FIX: this previously had no existence check at all, unlike
        # every other list_*_for_trial/get_* method. Calling
        # GET /api/trial/DOES-NOT-EXIST/flags silently returned 200 {}
        # instead of a 404, while the structurally identical
        # /reports endpoint correctly 404'd -- confirmed by direct testing.
        # A client had no way to distinguish "this trial has zero flags"
        # from "this trial doesn't exist" via this endpoint.
        if trial_id not in self.trials:
            raise IntegrityCheckError(f"unknown trial_id '{trial_id}'")
        return {fid: dict(f) for fid, f in self.flags.items() if f["trial_id"] == trial_id}

    # ---------------- views ----------------

    def get_trial(self, trial_id):
        if trial_id not in self.trials:
            raise IntegrityCheckError(f"unknown trial_id '{trial_id}'")
        return dict(self.trials[trial_id])

    def get_report(self, report_id):
        if report_id not in self.integrity_reports:
            raise IntegrityCheckError(f"unknown report_id '{report_id}'")
        return dict(self.integrity_reports[report_id])

    def list_reports_for_trial(self, trial_id):
        """
        MISSING FEATURE (now added): the spec explicitly requires an
        'Integrity dashboard (score + flags per trial)'. The original
        implementation only stored the *latest* score/verdict on the trial
        record itself and had no way to list all reports (with their full
        flag detail) for a given trial. Without this, a frontend dashboard
        could show a verdict but never show *why* -- i.e. never show the
        flags -- which is exactly what the spec asks for.
        """
        if trial_id not in self.trials:
            raise IntegrityCheckError(f"unknown trial_id '{trial_id}'")
        return {
            rid: dict(r) for rid, r in self.integrity_reports.items()
            if r["trial_id"] == trial_id
        }

    def list_trials(self):
        return {tid: dict(t) for tid, t in self.trials.items()}

    # ---------------- internals ----------------

    def _build_prompt(self, trial, current_registry, paper, preprint_text):
        return f"""
You are a clinical trial data integrity specialist. Assess whether this published result is consistent with the pre-registered protocol.

PRE-REGISTERED PROTOCOL (snapshot at registration):
{trial['protocol_snapshot'][:2000]}

CURRENT REGISTRY STATE (may show amendments):
{current_registry[:2000]}

PRE-REGISTERED HYPOTHESIS: {trial['hypothesis']}
PRE-REGISTERED PRIMARY ENDPOINTS: {', '.join(trial['endpoints'])}
EXPECTED SAMPLE SIZE: {trial['expected_n']}

PUBLISHED PAPER:
{paper[:3000]}

PREPRINT (if available):
{preprint_text}

Check for:
1. Outcome switching (different primary endpoints in paper vs registration)
2. Sample size inconsistencies -- but a documented, dated DSMB
   (Data Safety Monitoring Board) early-stopping decision is a LEGITIMATE
   reason for a smaller-than-planned sample size and must NOT be flagged
   as sample_size_discrepancy.
3. Post-hoc subgroup analyses presented as primary
4. Implausible p-values or effect sizes
5. Undisclosed protocol amendments (present in the current registry but
   absent from the original protocol snapshot, with no corresponding
   disclosure in the paper)

Respond ONLY with JSON (no markdown fences, no preamble):
{{
  "integrity_score": 0-100,
  "flags": [
    {{
      "type": "outcome_switching" | "sample_size_discrepancy" | "p_hacking" | "undisclosed_amendment" | "other",
      "severity": "critical" | "moderate" | "minor",
      "description": "one sentence"
    }}
  ],
  "endpoints_match": true or false,
  "sample_size_consistent": true or false,
  "overall_verdict": "clean" | "concerns" | "suspected_fraud",
  "confidence": "high" | "medium" | "low",
  "summary": "2-3 sentence overall assessment"
}}
""".strip()

    @staticmethod
    def _parse_llm_json(raw):
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:]
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise IntegrityCheckError(f"LLM did not return valid JSON: {e}\nRaw: {raw[:500]}")

    @staticmethod
    def _validate_llm_result(result):
        required = [
            "integrity_score", "flags", "endpoints_match",
            "sample_size_consistent", "overall_verdict", "confidence", "summary",
        ]
        missing = [k for k in required if k not in result]
        if missing:
            raise IntegrityCheckError(f"LLM JSON missing required keys: {missing}")
        if result["overall_verdict"] not in ("clean", "concerns", "suspected_fraud"):
            raise IntegrityCheckError(f"invalid overall_verdict: {result['overall_verdict']}")
        if result["confidence"] not in ("high", "medium", "low"):
            raise IntegrityCheckError(f"invalid confidence: {result['confidence']}")

        # BUG FIX: the original validation only checked that the top-level
        # keys were present. It never checked that integrity_score was
        # actually a number in [0, 100], nor that each flag object had the
        # required type/severity/description fields with valid enum values.
        # A real LLM (unlike the deterministic mock) can and will
        # occasionally return integrity_score as a string, out of range, or
        # omit a field inside a flag object -- without this check that bad
        # data would have been written straight into contract state.
        score = result["integrity_score"]
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            raise IntegrityCheckError(f"integrity_score must be numeric, got {type(score).__name__}")
        if not (0 <= score <= 100):
            raise IntegrityCheckError(f"integrity_score out of range 0-100: {score}")

        if not isinstance(result["flags"], list):
            raise IntegrityCheckError("flags must be a list")
        valid_types = {"outcome_switching", "sample_size_discrepancy", "p_hacking", "undisclosed_amendment", "other"}
        valid_severities = {"critical", "moderate", "minor"}
        for i, flag in enumerate(result["flags"]):
            if not isinstance(flag, dict):
                raise IntegrityCheckError(f"flags[{i}] must be an object")
            flag_missing = [k for k in ("type", "severity", "description") if k not in flag]
            if flag_missing:
                raise IntegrityCheckError(f"flags[{i}] missing keys: {flag_missing}")
            if flag["type"] not in valid_types:
                raise IntegrityCheckError(f"flags[{i}] invalid type: {flag['type']}")
            if flag["severity"] not in valid_severities:
                raise IntegrityCheckError(f"flags[{i}] invalid severity: {flag['severity']}")

        if not isinstance(result["endpoints_match"], bool):
            raise IntegrityCheckError("endpoints_match must be a boolean")
        if not isinstance(result["sample_size_consistent"], bool):
            raise IntegrityCheckError("sample_size_consistent must be a boolean")
