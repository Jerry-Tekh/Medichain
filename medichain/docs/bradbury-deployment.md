# MediChain Bradbury Deployment Runbook

This note records the GenLayer Bradbury deployment fix and the verification
commands used from this workspace.

## Network

- CLI package: `genlayer@0.39.2`
- Network alias: `testnet-bradbury`
- Network name: `Genlayer Bradbury Testnet`
- Chain ID: `4221`
- RPC: `https://rpc-bradbury.genlayer.com`

## Successful Deployment

- Transaction: `0xff12089804b1773c0858495e194e8b206c32b4e69e4ffe0ad2c37eb4adc0d18f`
- Contract: `0x9c6D4d30F89f8701C8a4E63902880D52C5269523`
- Receipt status: `ACCEPTED`
- Consensus result: `AGREE`
- Execution result: `FINISHED_WITH_RETURN`

## Root Causes Fixed

1. The adapter used `py-genlayer:test`, which Bradbury rejects.
2. The adapter used `@gl.contract`, but Bradbury exposed `gl.Contract`.
3. Storage included `TreeMap[str, dict]`, which blocks schema-safe deploys.
4. Storage included plain `int` values, which Bradbury rejected for persisted fields.
5. The constructor assigned `TreeMap[str, ...]()` values, which Bradbury rejected at runtime.

## Adapter Rules

1. The first line must pin the runner:

   ```python
   # { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
   ```

2. Do not use `py-genlayer:test`, `py-genlayer:latest`, or unversioned `py-genlayer`.
3. Use `class MediChain(gl.Contract)` for the current Bradbury runner.
4. Store structured arrays/objects as JSON strings inside primitive `TreeMap` values.
5. Use `bigint` or sized integers for persisted numeric fields.
6. Use `u256` for money-like values such as integrity bonds.
7. Leave annotated `TreeMap` fields to Bradbury storage initialization.

## Local Verification

Run these checks before deploy:

```bash
python3 medichain/scripts/check_genlayer_adapter.py
python3 -m py_compile \
  medichain/contract/genlayer_adapter.py \
  medichain/scripts/check_genlayer_adapter.py \
  medichain/tests/test_integration.py
```

The standalone check verifies the pinned runner, the `gl.Contract` class,
schema-safe storage annotations, and absence of constructor-level `TreeMap`
assignments.

## Deploy Command

The successful Bradbury deploy used explicit fee/timeunit allocation:

```bash
set -a
. ./.env.local
set +a

npx -y genlayer@0.39.2 deploy \
  --contract medichain/contract/genlayer_adapter.py \
  --args "$TREASURE_ADDRESS" \
  --fees '{"distribution":{"leaderTimeunitsAllocation":"1000","validatorTimeunitsAllocation":"1000","rotations":["0"]}}'
```

The earlier default-fee retry produced `LEADER_TIMEOUT`; the explicit allocation
allowed validators to accept the deployment.

## Schema Verification

```bash
npx -y genlayer@0.39.2 schema 0x9c6D4d30F89f8701C8a4E63902880D52C5269523
```

The schema call returned constructor parameter `treasury_address` and all public
view/write methods, including `register_trial`, `submit_results`,
`resolve_appeal`, `submit_flag`, `list_trials`, and `get_treasury_address`.

## Read Verification

```bash
npx -y genlayer@0.39.2 call \
  0x9c6D4d30F89f8701C8a4E63902880D52C5269523 \
  get_treasury_address
```

The read call completed successfully against the deployed Bradbury contract.
