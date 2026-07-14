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
