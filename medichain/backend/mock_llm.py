"""
Deterministic mock LLM client for local testing.

In production, gl.exec_prompt() sends the integrity-check prompt to
GenLayer's decentralized LLM validators (Optimistic Democracy + the
Equivalence Principle reconcile their answers). This sandbox has no
access to that network, and real LLM calls are non-deterministic, which
would make automated tests flaky.

Instead, this module implements simple, deterministic keyword-based
rules that mirror exactly what the prompt asks a real LLM to check. It
exists ONLY so the end-to-end wiring (contract logic <-> backend API
<-> frontend) can be verified reproducibly without a live model. It is
not a substitute for the real integrity analysis -- see README.md.
"""

import json


def mock_llm_client(prompt: str) -> str:
    """Callable[[str], str] matching the MediChainContract llm_client interface."""

    result = {
        "integrity_score": 70,
        "flags": [{
            "type": "other",
            "severity": "minor",
            "description": "Mock evaluator fallback branch: no strong distinguishing signal found in fixture text.",
        }],
        "endpoints_match": True,
        "sample_size_consistent": True,
        "overall_verdict": "concerns",
        "confidence": "medium",
        "summary": "Mock evaluator could not confidently classify this case; falling back to a neutral 'concerns' verdict.",
    }

    lower = prompt.lower()

    # --- Outcome-switching / Theranos-style signal ---
    if "diagnostic concordance" in lower and "satisfaction survey" in lower:
        result = {
            "integrity_score": 18,
            "flags": [
                {
                    "type": "outcome_switching",
                    "severity": "critical",
                    "description": "Published paper reports a patient satisfaction survey while the pre-registered primary endpoint was diagnostic concordance against a reference lab panel.",
                },
                {
                    "type": "sample_size_discrepancy",
                    "severity": "critical",
                    "description": "Published sample size (n=55) is far below the pre-registered n=200 with no documented stopping rule.",
                },
            ],
            "endpoints_match": False,
            "sample_size_consistent": False,
            "overall_verdict": "suspected_fraud",
            "confidence": "high",
            "summary": (
                "The published outcome does not match the pre-registered primary endpoint, "
                "and the sample size dropped sharply with no documented justification. This "
                "pattern is consistent with outcome switching to avoid reporting an "
                "unfavorable primary result."
            ),
        }

    # --- Legitimate DSMB early-stopping signal ---
    elif "dsmb" in lower and "pre-specified" in lower and "o'brien-fleming" in lower and "consistent with" in lower:
        result = {
            "integrity_score": 92,
            "flags": [],
            "endpoints_match": True,
            "sample_size_consistent": True,
            "overall_verdict": "clean",
            "confidence": "high",
            "summary": (
                "The reduced sample size is fully explained by a documented, dated DSMB "
                "early-stopping decision that references the original pre-specified "
                "O'Brien-Fleming efficacy boundary. The primary endpoint is unchanged. "
                "This is a textbook legitimate early stop, not a fraud signal."
            ),
        }

    return json.dumps(result)
