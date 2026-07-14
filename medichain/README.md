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
