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
   while the structurally identical `/reports` endpoint correctly
   returned `404` for the exact same nonexistent trial — confirmed with
   a side-by-side curl comparison before fixing. A client had no way to
   distinguish "this trial has zero flags" from "this trial doesn't
   exist." Fixed in both the local contract and the GenLayer adapter.

4. **Missing input validation**: `submit_results` accepted an empty
   `publication_url` with no check, and `submit_flag` accepted empty
   `submitter`/`description` strings with no check. Both fixed with the
   same "not empty" validation pattern already used for `trial_id`.

5. **Frontend race condition.** `refreshTrials()` is triggered from
   several places (page load, every form submit, the Refresh button, the
   Details toggle) and does multiple awaited fetches per trial inside its
   loop. If two calls overlap — e.g. the page-load auto-refresh is still
   in flight when a user clicks something — both calls clear and rebuild
   the table concurrently. Reproduced directly: firing 3 overlapping
   `refreshTrials()` calls against a 2-trial backend produced **6 rows**
   instead of 2. Fixed with a generation-token guard (each call captures
   a token and bails out before touching the DOM if a newer call has
   since started); re-ran the identical reproduction after the fix and
   got exactly 2 rows. This is now a permanent regression check in
   `dom_test.js` (17/17 checks passing).

All of the above were found by actually running code (direct
reproduction scripts, not just reasoning about it) and are now covered
by permanent regression tests — 27/27 in `pytest`, 17/17 in the DOM
click-through suite.

## Follow-up pass: report detail view + full responsive design

**Gap found:** even after adding flag badges to the dashboard, there was
still no way to read a report's actual `summary` text, its `confidence`,
which `publication_url` it came from, or to see more than the single
latest report if a trial had multiple submissions — the backend already
supported all of this (`/api/trial/{id}/reports`) but nothing in the UI
surfaced it. Same for whistleblower flags: only a count was shown, never
the submitter/description/evidence. Fixed by adding a per-row **Details**
toggle that expands to show full report history and whistleblower flag
detail, all through `escapeHtml()` like everything else on the dashboard.
Verified with 3 new DOM checks (16/16 total now) that actually click the
button, read the expanded row's real text content, and click again to
confirm it collapses.

**Responsive design audit.** The layout previously wasn't actually
responsive despite having a viewport meta tag — a few concrete bugs:

- `.api-config` (the API-base bar in the header) was a non-wrapping flex
  row with a **fixed 220px input**. On a narrow phone, "API base:" text +
  220px input + status dot together exceed the viewport width, forcing
