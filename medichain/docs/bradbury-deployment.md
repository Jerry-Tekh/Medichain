# MediChain Bradbury Deployment Runbook

## Network

- CLI: `genlayer@0.39.2`
- Network alias: `testnet-bradbury`
- Chain ID: `4221`
- RPC: `https://rpc-bradbury.genlayer.com`

## Current Production Contract

- Contract: `0x05ECcb86D107c4AbC1ebb4cb4C1E38182c38213C`
- Deployment transaction:
  `0x7fb9af63bf2238f0a7f5d5a1aa08b772088a9a38253ff97166b65673e6beeba0`
- Receipt: `ACCEPTED`
- Consensus: `AGREE`
- Execution: `FINISHED_WITH_RETURN`
- Schema: verified
- Signed deployment ceiling: `2949336047864250` wei (about `0.00295 GEN`)
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
4. streams public deployment output to `medichain/.deploy/`
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
text. The contract binds snapshots to their submitted URLs and runs only the
clinical assessment through `prompt_comparative`. This preserves substantive
validator consensus while avoiding the `LEADER_TIMEOUT` caused by GenVM web
rendering.

Live workflow verification on this deployment:

- Registration transaction:
  `0x49b55c3c721af208824a8778fb0e2bcb94e185162e416b7ece2828abd70af416`
  (`AGREE`, `FINISHED_WITH_RETURN`, ceiling about `0.000375 GEN`)
- Report transaction:
  `0x62e306e8abe9272a390bd99ba062328ebc5773a2e7ba3c0ee8bcdd902376f8c4`
  (`AGREE`, `FINISHED_WITH_RETURN`, ceiling about `0.00137 GEN`)

## Verification

```bash
python3 medichain/scripts/check_genlayer_adapter.py

npx -y genlayer@0.39.2 schema \
  0x05ECcb86D107c4AbC1ebb4cb4C1E38182c38213C \
  --rpc https://rpc-bradbury.genlayer.com

npx -y genlayer@0.39.2 call \
  0x05ECcb86D107c4AbC1ebb4cb4C1E38182c38213C \
  get_owner \
  --rpc https://rpc-bradbury.genlayer.com

npx -y genlayer@0.39.2 call \
  0x05ECcb86D107c4AbC1ebb4cb4C1E38182c38213C \
  get_treasury_address \
  --rpc https://rpc-bradbury.genlayer.com
```

The schema must contain `register_trial`, `submit_results`, `submit_flag`,
`resolve_appeal`, `get_owner`, and `get_treasury_address`.

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

- `0xebb0590f54Aaf1bA1Cfd544325307759c1F79e50`: schema-safe adapter without
  owner-restricted writes
- `0x9c6D4d30F89f8701C8a4E63902880D52C5269523`: initial schema-fix deployment

They are retained only as deployment history and must not be used by the
production API.
