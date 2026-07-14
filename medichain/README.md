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
