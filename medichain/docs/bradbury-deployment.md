# MediChain Bradbury Deployment Runbook

## Network

- CLI: `genlayer@0.39.2`
- Network alias: `testnet-bradbury`
- Chain ID: `4221`
- RPC: `https://rpc-bradbury.genlayer.com`

## Current Verified Contract

- Contract: `0x6207D84A866919Daa876b902E3ab51F5560F10CB`
- Deployment transaction:
  `0xc9c572ddc5e613765eb84667fd96ffac1c05b715c142846e05e31166908278d9`
- Receipt: `ACCEPTED`
- Consensus: `AGREE`
- Execution: `FINISHED_WITH_RETURN`
- Schema: verified
- Signed deployment ceiling: `3802801463972700` wei (about `0.00380 GEN`)
- Owner: `0x1847d40a1fc2b69101d943f23ea35bd3774889d7`
- Treasury: `0x1847d40a1fc2b69101d943f23ea35bd3774889d7`

This deployment stores the Render relayer as the contract owner. Every
state-changing method checks `gl.message.sender_address` against that owner,
so callers cannot bypass the API's wallet authentication and role rules.

## Runner Requirements

The contract must start with:

```python
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
```

Do not use `py-genlayer:test`, `py-genlayer:latest`, or an unversioned runner.
For this pinned Bradbury runner:

- declare the contract with `class MediChain(gl.Contract)`
- use `gl.message.sender_address`, not `sender_account`
- use primitive `TreeMap` values
- use `bigint` or sized integer aliases for stored integers
- use `u256` for money-like values
- leave annotated `TreeMap` fields to storage initialization

## Deployment

Store `PRIVATE_KEY` and `TREASURE_ADDRESS` in the git-ignored `.env.local`,
then run:

```bash
set -a
. ./.env.local
set +a

GENLAYER_CLI_COMMAND='npx -y genlayer@0.39.2' \
  python3 medichain/scripts/deploy_bradbury.py
```

The script:

1. validates secret formats without printing them
2. creates an isolated temporary GenLayer keystore
3. reuses the local npm package cache
4. persists the public transaction hash immediately after submission
5. requires `AGREE` and `FINISHED_WITH_RETURN`
6. verifies the deployed schema
7. reads and reports treasury and owner addresses

Every deployment and write is signed by the bounded transaction helper. It
adds a 25 percent gas-limit buffer and refuses to sign when
`gas * gasPrice + value` exceeds `500000000000000000` wei (`0.5 GEN`).

Trial registration does not ask GenVM to render the large ClinicalTrials.gov
page. The backend fetches the official API record, creates a canonical
snapshot, and the contract deterministically validates its NCT identifier and
required protocol fields before storage.

Result submission also receives bounded backend snapshots for the current
registry, publication, and optional preprint. The backend enforces public HTTPS
destinations, validates redirects, caps responses, and sanitizes documents to
text. Publication and preprint URLs are trimmed once before fetching, then the
same exact canonical URL is sent as the contract argument and snapshot
`source_url`; redirects are retained only as `resolved_url`. The backend
rejects a divergent snapshot before signing a transaction.

On `FINISHED_WITH_ERROR`, the bounded transaction helper uses
`debugTraceTransaction` when available and returns only the final sanitized
contract exception plus the transaction hash. Trace retrieval failure falls
back to the generic Bradbury rejection without hiding the hash.

The contract binds snapshots to their submitted URLs and runs only the
clinical assessment non-deterministically. Every validator reruns one
structured JSON assessment and deterministic code compares the overall
verdict, score tolerance, endpoint and sample-size decisions, actionable-fraud
state, and critical flag types. This avoids both the extra comparator LLM call
and the `LEADER_TIMEOUT` caused by GenVM web rendering.

## July 19, 2026 Write Rejections

Registration transaction
`0x24799496ef8b2218f6e8b4ccf9da984f7b421b7e3eff3ecd2ea8b0b50cc8122c`
was a repeated request for trial ID `MEDICHAIN-USER-20260719-001`. Its trace
ended with `trial_id 'MEDICHAIN-USER-20260719-001' already registered`; the
first registration had already succeeded.

The rejected report transaction
`0xdfaa28cc37c60432b4919886dba644ee34062a9b0bd9840fb1ad990d2503e999`
decoded to:

```text
publication_url: " https://pubmed.ncbi.nlm.nih.gov/32445440/"
publication_snapshot.source_url: " https://pubmed.ncbi.nlm.nih.gov/32445440/"
publication_snapshot.resolved_url: "https://pubmed.ncbi.nlm.nih.gov/32445440/"
preprint_url: ""
```

Both submitted/source values contained one leading space. The deployed
contract strips the snapshot `source_url` before comparing it with the raw
`publication_url`, so the normalized snapshot value no longer equaled the
untrimmed argument. This deterministic input rejection happened before an LLM
call. Backend and frontend URL canonicalization now remove that whitespace
before any future snapshot/write boundary; the contract remains unchanged.

Live workflow verification on this deployment:

- Registration transaction:
  `0xa029c5929531e9312be71f0f429baa8b6098e564fd2e81a39d68b704466fd491`
  (`AGREE`, `FINISHED_WITH_RETURN`, ceiling about `0.000441 GEN`)
- Report transaction:
  `0x6d387e690e94a455d45336b026f8692a8b2f433d697fc7d2aa8f36e34349ad89`
  (`AGREE`, `FINISHED_WITH_RETURN`, ceiling about `0.00161 GEN`)

The stored verification report
`MEDICHAIN-VERIFY-REPORT-20260718-001` is `clean`, `high` confidence, with
matching endpoints and sample size.

## Verification

```bash
python3 medichain/scripts/check_genlayer_adapter.py

npx -y genlayer@0.39.2 schema \
  0x6207D84A866919Daa876b902E3ab51F5560F10CB \
  --rpc https://rpc-bradbury.genlayer.com

npx -y genlayer@0.39.2 call \
  0x6207D84A866919Daa876b902E3ab51F5560F10CB \
  get_owner \
  --rpc https://rpc-bradbury.genlayer.com

npx -y genlayer@0.39.2 call \
  0x6207D84A866919Daa876b902E3ab51F5560F10CB \
  get_treasury_address \
  --rpc https://rpc-bradbury.genlayer.com
```

The schema must contain `register_trial`, `submit_results`, `submit_flag`,
`resolve_appeal`, `get_owner`, and `get_treasury_address`.

Render is switched only when `/api/health` reports this same contract address.
Dashboard environment values can override `render.yaml`.

## Diagnosed Consensus Failure

Report transaction
`0x7858cec44b5d996022e6a2c59226f38708a50dbb4db09488e12448f27f08908d`
on experimental contract
`0x90310690724e359F0cEc0825A45F3e8a95f0B411` returned `NO_MAJORITY`.
The five revealed votes were two `TIMEOUT`, two
`DETERMINISTIC_VIOLATION`, and one `AGREE`. The generic comparative wrapper
was replaced with independent assessment plus deterministic comparison, and
the backend now rejects every non-`AGREE` receipt.

Deployment transaction
`0x06e1a739880614473c4c3cbe2777a2a76ed1ab5148d2d89082cf38ef282cb95b`
ended in `LEADER_TIMEOUT` with no leader public data and no validator votes.
No contract was created. The deployment helper now persists the submitted hash
before waiting, and the single bounded retry succeeded.

## Failed Ownership Attempt

Transaction
`0x741f5a84226038174f5d9fae3e22f45c02872ae167ed5fd71f8ebf04db930d7b`
reached `ACCEPTED` and `AGREE` but ended as `FINISHED_WITH_ERROR`. Its execution
trace showed:

```text
AttributeError: 'MessageType' object has no attribute 'sender_account'
```

The unusable candidate address was
`0xD884E048B0671b898A242764a72Fb7A0c65D1d69`. The contract was corrected to
use the pinned runner's `sender_address` field before the successful deployment.

Inspect future failures with:

```bash
npx -y genlayer@0.39.2 trace <transaction-hash> \
  --rpc https://rpc-bradbury.genlayer.com
```

An accepted transaction is not sufficient. Never configure Render with a new
address until execution is `FINISHED_WITH_RETURN` and schema/read checks pass.

## Previous Contracts

- `0x05ECcb86D107c4AbC1ebb4cb4C1E38182c38213C`: previous owned production
  deployment using the generic comparative wrapper
- `0x90310690724e359F0cEc0825A45F3e8a95f0B411`: experimental structured-output
  deployment whose report test returned `NO_MAJORITY`
- `0xebb0590f54Aaf1bA1Cfd544325307759c1F79e50`: schema-safe adapter without
  owner-restricted writes
- `0x9c6D4d30F89f8701C8a4E63902880D52C5269523`: initial schema-fix deployment

They are retained only as deployment history and must not be used by the
production API.
