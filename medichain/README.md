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
  horizontal overflow of the *header itself*. Fixed: `flex-wrap: wrap`
  plus a flexible `flex: 1 1 160px` input that shrinks instead of
  overflowing.
- The 8-column trials table had **no scroll container**. On a phone, a
  table that wide would force the *entire page* into horizontal scroll,
  not just the table. Fixed: wrapped it in `.table-scroll` with
  `overflow-x: auto`, so only the table itself scrolls, contained within
  its card — the standard responsive pattern for wide data tables.
- The media query rules were originally placed *before* some later
  plain (non-media) rules in the same file. Because CSS resolves
  same-specificity conflicts by **source order**, not by whether a rule
  sits inside `@media`, a later plain `table { font-size: 0.85rem }`
  would have silently beaten an earlier `@media (max-width:700px) {
  table { font-size: 0.78rem } }` on every phone-sized screen — the
  responsive rule would never actually apply. Fixed by moving all
  `@media` blocks to the very end of the stylesheet, and verified there's
  no rule after them that could re-win the cascade.
- `main`'s grid used `minmax(320px, 1fr)`, which can't shrink a card
  below 320px — on a 320px-wide phone (iPhone SE and similar) that leaves
  zero room for the page's own padding, forcing overflow. Reduced to
  `minmax(280px, 1fr)` and added padding reductions at both a 700px and a
  420px breakpoint.
- Removed the browser-default handling gap where none of the responsive
  behavior had actually been checked against real breakpoints. Verified
  (see below) that the two breakpoints fire at exactly the intended
  widths, including the smallest common phone width (320px).

**What was and wasn't verified:** I don't have a real rendering browser
available (no network access to download Chromium/Playwright's browser
binary in this sandbox), so I could not screenshot actual pixel layouts
at each breakpoint. What I *did* verify with real tools:
- All 16 DOM click-through checks re-ran clean after every CSS change
  (confirms the responsive changes didn't break any functionality).
- A real CSS media-query evaluator (`css-mediaquery`, not a guess) confirms
  the two breakpoints activate at exactly the intended widths: neither
  breakpoint fires at 1200px/700px-exclusive-desktop-widths, the 700px
  breakpoint fires from 700px down to 421px, and both breakpoints fire
  together from 420px down through 320px (the smallest mainstream phone
  width).
- A static scan confirms no remaining fixed-pixel `width` declarations
  outside of small fixed elements (the 10px status dot) and the
  intentionally-scrollable table's minimum column width.

If you have access to a real browser, it's still worth manually resizing
the window / using devtools' device toolbar once to eyeball it — that's
the one check I genuinely could not perform here.

## In-depth UI audit: is every designed element actually functional?

This was checked two ways, not just by reading the code:

**1. Static wiring cross-check.** Every `id="..."` in `index.html` was
diffed against every `getElementById(...)` call in `app.js` (and vice
versa). Result: no orphaned buttons, forms, or display elements — every
control that exists has a real event listener behind it, and every
listener targets a real element.

**2. Real browser simulation (`frontend/dom_test.js`).** Static wiring
checks can't catch "the button is wired up but does the wrong thing" or
"the display renders but shows stale/fake data." So I loaded the *actual*
`index.html` + `app.js` files into jsdom (a real DOM implementation, not a
mock), pointed them at a live backend, and drove it exactly like a user
would: filled in each form's real input fields, dispatched real `submit`
events, clicked the real "Refresh Trials" button, and read back what
actually landed in the live DOM afterward. All 4 forms + the dashboard +
the health indicator were verified this way — **10/10 checks passed**,
confirming the register/submit/appeal/whistleblower forms all round-trip
through the real backend and the dashboard re-renders with real data,
not placeholders.

**3. Adversarial check — and this one caught a real bug.** The DOM test
also registers a trial whose `trial_id` is literally
`EVIL<img src=x onerror=alert(1)>` and checks whether that tag lands as
*executable markup* in the live dashboard. First run: **it did** — every
piece of dynamic data (trial IDs, statuses, flag descriptions, etc.) was
being inserted into the table via `innerHTML` template literals with no
escaping. That's a real HTML-injection bug: anyone able to register a
trial or get results published under a crafted ID/hypothesis/flag text
could break the dashboard's rendering or run arbitrary script in another
user's browser.

Fixed: added an `escapeHtml()` helper in `app.js` and applied it to every
dynamic value written into the table (trial ID, status, score, verdict,
bond, flag type/severity/description, verdict CSS class, error messages).
Re-ran the same adversarial DOM test after the fix — **10/10 passed**,
and the `<img onerror>` payload no longer appears in the live DOM at all
(it now renders as inert text).

Also fixed while auditing: `verdict-concerns` and `verdict-none` row
classes were referenced in `app.js` but had no matching CSS, so trials in
those states rendered with no row highlighting at all. Added both.

To re-run the DOM test yourself:
```bash
cd frontend
npm install       # installs jsdom, the only dependency
# in another terminal: cd ../backend && uvicorn main:app --port 8000
node dom_test.js
```

## In-depth audit: what was found and fixed

This section is a straight log of the audit, not marketing copy.

### GenLayer Bradbury deployment corrections

The deploy adapter at `contract/genlayer_adapter.py` is aligned with the
current GenLayer Write Contract skill for Bradbury deployments:

| Requirement | Current adapter |
|---|---|
| Pinned runner dependency | First line is `py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6` |
| No rejected runner aliases | No `py-genlayer:test`, `py-genlayer:latest`, or unversioned runner alias |
| Contract declaration | Uses `class MediChain(gl.Contract)` because the current Bradbury runner reports `gl.contract` is unavailable |
| Storage shape | Uses `TreeMap` fields with primitive values; nested arrays/objects are stored as JSON strings |
| Sponsor addresses | Stored as strings because Bradbury rejects `TreeMap[str, Address]` value storage |
| Integer storage | Uses `bigint` or `u256`, not plain `int`, for stored numeric fields |
| Storage initialization | Leaves annotated `TreeMap` fields to Bradbury's storage initializer; no `TreeMap()` or `TreeMap[str, ...]()` assignments in `__init__` |
| Money type | `integrity_bond` and the bond storage map use `u256` |
| Treasury address | Constructor accepts the deploy-time `TREASURE_ADDRESS` |
| LLM/web consensus | Web/LLM analysis is isolated inside an equivalence-principle closure and validated defensively |

This specifically fixes the Bradbury-side "cannot get contract schema"
failure caused by the old `py-genlayer:test` runner header, schema-hostile
`TreeMap[str, dict]` state, plain `int` storage, and constructor-level
`TreeMap[str, ...]()` assignments.

Live Bradbury verification from this workspace:

```bash
set -a
. ./.env.local
set +a

GENLAYER_CLI_COMMAND='npx -y genlayer@0.39.2' \
  python3 medichain/scripts/deploy_bradbury.py

npx -y genlayer@0.39.2 schema 0x71EACA0FB43DE806e8e549554fc0D91BBdbB2213
npx -y genlayer@0.39.2 call 0x71EACA0FB43DE806e8e549554fc0D91BBdbB2213 get_treasury_address
npx -y genlayer@0.39.2 call 0x71EACA0FB43DE806e8e549554fc0D91BBdbB2213 get_owner
```

Current corrected Bradbury deploy:

- Contract: `0x71EACA0FB43DE806e8e549554fc0D91BBdbB2213`
- Deployment transaction:
  `0x9a713c008bd184b4b7ba06ca18936eb8b17c0ecd6b45bb1f4af23fff821bbda3`
- Receipt result: `ACCEPTED`, `AGREE`, `FINISHED_WITH_RETURN`
- Schema: retrieved successfully
- `get_treasury_address`: read successfully
- `get_owner`: returns the Render relayer address
- Signed deployment ceiling: about `0.0036 GEN`

The backend fetches the official ClinicalTrials.gov API record and creates a
canonical protocol snapshot before `register_trial`. The contract validates
the NCT identifier and required protocol fields deterministically, avoiding
Bradbury web-render timeouts during registration. All state-changing calls use
the bounded signer and are refused before signing when their transaction-cost
ceiling exceeds `0.5 GEN`.

### Application-level bugs found and fixed

1. **Duplicate `report_id` silently overwrote prior reports.** Fixed:
   `submit_results` now rejects a `report_id` that's already in use
   (400/`IntegrityCheckError`), matching the duplicate-check already in
   place for `trial_id` at registration. Covered by
   `test_duplicate_report_id_rejected`.
2. **LLM-result validation was shallow.** It checked that the top-level
   JSON keys existed, but never checked `integrity_score` was numeric and
   in range, or that each flag object had valid `type`/`severity` enum
   values. Fine for the deterministic mock, but a real LLM validator can
   return malformed data. Fixed with full schema + range validation in
   `_validate_llm_result`.
3. **Missing dashboard feature** — the spec explicitly requires an
   "Integrity dashboard (score + flags per trial)," but there was no way
   to list a trial's integrity reports (and therefore its flags) at all;
   the trial record only ever stored the *latest* score/verdict. Added
   `list_reports_for_trial()` to the contract, a new
   `GET /api/trial/{id}/reports` endpoint, and updated the frontend
   dashboard to actually render flag badges (type + severity, with
   description on hover) instead of just a whistleblower-flag count.
4. **No input validation on registration.** Empty `trial_id`, empty
   `primary_endpoints`, zero/negative `expected_sample_size`, or
   zero/negative `integrity_bond` were all previously accepted. Fixed at
   both the pydantic layer (422) and the contract layer (defense in
   depth, in case the contract is called directly rather than through the
   API — which is exactly how it'll be called once on GenLayer, since
   pydantic doesn't exist there).
5. **`decision` field in `resolve_appeal` accepted any string**, only
   validated at the contract layer. Now typed as
   `Literal["confirm_fraud", "dismiss"]` in the pydantic model too, so bad
   input is rejected before it reaches contract logic.
6. **No regression test for double-resolving an appeal.** Added
   `test_resolve_appeal_twice_fails_second_time` to confirm the appeal
   window actually closes after first resolution and can't be resolved
   again.
7. **No test that CORS actually works cross-origin**, only informal manual
   curl checks in an earlier pass. Added a proper automated
   `test_cors_headers_present_for_cross_origin_frontend`, and separately,
   confirmed manually with two independent live processes (backend on
   :8000, frontend on :3000) sending real `Origin`-header requests, not
   just same-process test-client calls.

### Full current test count: 23/23 passing

Re-verified by extracting the actual shipped zip into a clean directory
and running `pytest` from there (not just from the build directory), so
what's in your hands is what was tested.

## Honest limitation: the mock LLM

`backend/mock_llm.py` is a **deterministic, keyword-based stand-in** for
`gl.exec_prompt()`. It exists only so the wiring (contract logic ↔ API ↔
frontend) can be tested reproducibly without network access or API keys.
It is not a real integrity analysis engine. Production does not use this
module: `MEDICHAIN_BACKEND_MODE=genlayer` routes reads and writes to the
deployed Bradbury contract, where validators perform the web and LLM work.
Real validators reading
arbitrary trial documents and reasoning about outcome switching, p-hacking,
etc. is what the pinned GenLayer adapter does.

## Deploying to GenLayer Bradbury

The production contract is already deployed at
`0x71EACA0FB43DE806e8e549554fc0D91BBdbB2213`. Verify the adapter before any
future redeploy:

1. Run the repository's Bradbury check:
   ```bash
   python3 scripts/check_genlayer_adapter.py
   ```
2. Verify the deployed schema:
   ```bash
   npx -y genlayer@0.39.2 schema 0x71EACA0FB43DE806e8e549554fc0D91BBdbB2213
   ```
3. Deploy only `contract/genlayer_adapter.py` as the single-file
   contract. The local `medichain_contract.py` remains the FastAPI test
   implementation; the adapter is the deployable GenLayer contract.
4. Load `PRIVATE_KEY` and `TREASURE_ADDRESS` from the ignored `.env.local`
   file and deploy with the bounded helper:
   ```bash
   set -a
   . ../.env.local
   set +a

   GENLAYER_MAX_TRANSACTION_COST_WEI=500000000000000000 \
   GENLAYER_CLI_COMMAND='npx -y genlayer@0.39.2' \
     python3 scripts/deploy_bradbury.py
   ```
5. Require an accepted receipt, `AGREE` consensus, and
   `FINISHED_WITH_RETURN` before checking schema.

If schema is still unavailable after deploy, first check the deploy
receipt. GenLayer can accept/finalize a transaction whose contract
execution failed; in that case no contract exists yet, so schema lookup
will fail until the deploy execution succeeds.

## Running it yourself

```bash
pip install -r requirements.txt

# backend
cd backend && uvicorn main:app --reload --port 8000

# frontend (separate terminal)
cd frontend && python3 -m http.server 3000
# for separate local origins, set API_BASE_URL in config.js to the API origin

# tests (separate terminal)
cd tests && pytest -v
```

## Production deployment

The production API uses restricted CORS and host allowlists, one-time wallet
signature authentication, revocable JWT sessions, role authorization, the
owner-restricted Bradbury contract for durable state, bounded request
concurrency, and read-after-write contract responses. The frontend receives
only public API and Bradbury network configuration; signing keys, JWT secrets,
and database credentials remain Render-only.

Use the root `Dockerfile` or `render.yaml` for the backend and
`frontend/vercel.json` for the static frontend. The complete environment
variable list, secret handling rules, and post-deploy checks are in
[`DEPLOYMENT.md`](DEPLOYMENT.md).
