# MediChain — Decentralised Clinical Trial Data Integrity

A working local implementation of the MediChain spec: contract logic,
a REST backend, a browser frontend, and an automated test suite — plus
a GenLayer deployment adapter for GenLayer Studio.

## What's in this zip

```
medichain/
├── contract/
│   ├── medichain_contract.py   ← the actual business logic (source of truth)
│   └── genlayer_adapter.py     ← gl.Contract adapter for GenLayer Bradbury
├── backend/
│   ├── main.py                 ← FastAPI REST API (local simulation server)
│   ├── mock_fetcher.py         ← canned ClinicalTrials.gov/PubMed fixtures
│   └── mock_llm.py             ← deterministic mock LLM (see caveat below)
├── frontend/
│   ├── index.html / app.js / style.css   ← plain HTML/JS dashboard
├── tests/
│   └── test_integration.py     ← automated end-to-end test suite (10 tests)
└── requirements.txt
```

## What has actually been verified (and how)

I do **not** have network access to GenLayer, ClinicalTrials.gov, PubMed,
or an LLM API from this environment, so I could not test against the real
GenLayer network. Everything below **was actually executed**, not just
written:

1. **`pytest tests/test_integration.py` — 10/10 passing.**
   - Full user journey: register trial → submit results → integrity
     analysis → flagged → whistleblower flag → appeal resolution → bond
     slashed, run through FastAPI's real routing/validation/serialization
     stack (not just calling Python functions directly).
   - The Theranos-style outcome-switching case: confirms `verdict ==
     "suspected_fraud"`, `outcome_switching` flag present, and — critically
     — that the bond is **not** slashed until a separate `resolve_appeal`
     call (rule 7 from the spec).
   - The legitimate-DSMB-early-stopping case: confirms a documented,
     pre-specified early stop is **not** flagged as fraud, proving the
     "don't punish legitimate amendments" design goal actually holds in
     code, not just in the prompt text.
   - Static contract-agreement checks: every endpoint path and every form
     field name in `frontend/app.js` / `index.html` is diffed against the
     backend's real pydantic models and FastAPI routes. If a frontend
     field name ever drifts from the backend, this test fails immediately.

2. **Live server smoke test (real HTTP, real CORS, two separate
   processes).** I booted the FastAPI backend on port 8000 and the static
   frontend on port 3000 as two independent OS processes, then sent actual
   cross-origin HTTP requests (with an `Origin: http://127.0.0.1:3000`
   header) to prove the browser-served frontend can really reach the
   backend and that CORS is configured correctly — not just that
   in-process test calls work. Confirmed:
   - CORS preflight returns `access-control-allow-origin: *`
   - A cross-origin `POST /api/register_trial` succeeds and returns the
     protocol snapshot
   - A cross-origin `POST /api/submit_results` against the Theranos
     fixture returns `integrity_score: 18`, `verdict: suspected_fraud`,
     `confidence: high`, with `outcome_switching` and
     `sample_size_discrepancy` flagged as critical
   - Both static frontend files (`index.html`, `app.js`) serve with `200`

## Deeper logic/implementation audit (this pass)

Five more real bugs, found by tracing state transitions and firing actual
concurrent/adversarial calls rather than just re-reading the code:

1. **Terminal-state bond bug (the most serious one found so far).**
   Reproduced directly: register a trial → get it flagged as
   `suspected_fraud` → `resolve_appeal(confirm_fraud)` (bond slashed,
   `status: resolved_fraud`) → submit **another** results report against
   the same `trial_id`. Before the fix, this silently flipped `status`
   back to `"flagged"` and reopened `appeal_window_open`, even though the
   bond was already permanently slashed. If a regulator then dismissed
   that reopened appeal, `bond_status` would have flipped to `"released"`
   despite already being `"slashed"` — a real financial state-consistency
   bug. Fixed: `submit_results` now rejects any further submission once a
   trial has reached a terminal state (`resolved_fraud` / `resolved_clean`).
   Confirmed fixed with a test that reproduces the exact sequence above
   and asserts the second submission is rejected and the bond state is
   untouched.

2. **Dead fixture code that overstated test coverage.** The mock fixtures
   included `"...?current=true"` variants meant to represent a registry
   that had been silently amended between registration and submission —
   but `submit_results()` always re-fetches the *exact same URL string*
   stored at registration (correct behavior for the real contract; the
   same live URL can return different content at two points in time on
   GenLayer). Since the mock fetcher is a pure function of the URL
   string, those fixtures were never actually reachable — the
   "undisclosed protocol amendment via registry drift" scenario was never
   really exercised end-to-end, despite fixtures implying it was. I
   considered making the fetcher stateful (return the amended fixture on
   a URL's second visit) but rejected that: several tests intentionally
   reuse the same two scenario URLs for unrelated edge cases, so
   per-URL state would leak across tests and silently corrupt *other*
   tests' "registration-time" snapshots — confirmed this would happen by
   tracing exactly which tests share which URLs. Fixed by removing the
   dead fixtures and documenting the limitation directly in
   `mock_fetcher.py`'s docstring, and correcting the `undisclosed_amendment`
   flag that the mock LLM was hard-coding into the Theranos scenario's
   result (it no longer reflects anything the mock actually "detected").

3. **Inconsistent 404 handling.** `list_flags_for_trial` had no
   existence check at all, unlike every other per-trial lookup
   (`get_trial`, `get_report`, `list_reports_for_trial`).
   `GET /api/trial/DOES-NOT-EXIST/flags` silently returned `200 {}`
