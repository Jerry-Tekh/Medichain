#!/usr/bin/env python3
"""Deploy the MediChain v2 GenLayer contract to Bradbury testnet.

Reads PRIVATE_KEY, GENLAYER_KEYSTORE_PASSWORD, and GENLAYER_RPC_URL
from .env in the project root. Never prints the private key.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = ROOT / ".env"
if ENV_PATH.exists():
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("\"'")
            if key and value:
                os.environ.setdefault(key, value)

GENLAYER_ETHERS_MODULE = os.getenv("GENLAYER_ETHERS_MODULE", "")
if not GENLAYER_ETHERS_MODULE:
    npm_global = Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules"
    candidate = npm_global / "genlayer" / "node_modules" / "ethers" / "lib.esm" / "index.js"
    if candidate.exists():
        GENLAYER_ETHERS_MODULE = str(candidate)

GENLAYER_JS_MODULE = os.getenv("GENLAYER_JS_MODULE", "")
if not GENLAYER_JS_MODULE:
    npm_global = Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules"
    candidate = npm_global / "genlayer" / "node_modules" / "genlayer-js" / "dist" / "index.js"
    if candidate.exists():
        GENLAYER_JS_MODULE = str(candidate)

PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")
KEYSTORE_PASSWORD = os.getenv("GENLAYER_KEYSTORE_PASSWORD", "")
RPC_URL = os.getenv("GENLAYER_RPC_URL", "")
NETWORK = os.getenv("GENLAYER_NETWORK", "testnet-bradbury")
ACCOUNT_NAME = os.getenv("GENLAYER_ACCOUNT_NAME", "medichain-production")

if not PRIVATE_KEY:
    sys.exit("ERROR: PRIVATE_KEY is not set in .env")
if not KEYSTORE_PASSWORD:
    sys.exit("ERROR: GENLAYER_KEYSTORE_PASSWORD is not set in .env")
if not RPC_URL:
    sys.exit("ERROR: GENLAYER_RPC_URL is not set in .env")

SCRIPT_DIR = Path(__file__).resolve().parent
CONTRACT_PATH = SCRIPT_DIR / "genlayer_adapter_v2.py"
SETUP_SCRIPT = ROOT / "medichain" / "backend" / "setup_genlayer_account.mjs"
TX_SCRIPT = ROOT / "medichain" / "backend" / "genlayer_transaction.mjs"

for p in (CONTRACT_PATH, SETUP_SCRIPT, TX_SCRIPT):
    if not p.exists():
        sys.exit(f"ERROR: required file not found: {p}")


def run(cmd, stdin=None, extra_env=None):
    env = os.environ.copy()
    env.update(extra_env or {})
    result = subprocess.run(
        cmd,
        input=stdin,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result


print("Setting up GenLayer signer...")
print(f"GENLAYER_ETHERS_MODULE={GENLAYER_ETHERS_MODULE}")
setup_result = run(
    ["node", str(SETUP_SCRIPT)],
    stdin=json.dumps({
        "private_key": PRIVATE_KEY,
        "password": KEYSTORE_PASSWORD,
        "account_name": ACCOUNT_NAME,
        "network": NETWORK,
    }),
    extra_env={"GENLAYER_ETHERS_MODULE": GENLAYER_ETHERS_MODULE} if GENLAYER_ETHERS_MODULE else {},
)
if setup_result.returncode != 0:
    sys.exit(f"ERROR: signer setup failed\n{setup_result.stderr}")
print(setup_result.stdout.strip())

print("\nDeploying contract...")
print(f"GENLAYER_JS_MODULE={GENLAYER_JS_MODULE}")
tx_payload = json.dumps({
    "action": "deploy",
    "private_key": PRIVATE_KEY,
    "rpc_url": RPC_URL,
    "network": NETWORK,
    "max_transaction_cost_wei": "500000000000000000",
    "contract_path": str(CONTRACT_PATH),
    "args": [],
})

tx_result = run(
    ["node", str(TX_SCRIPT)],
    stdin=tx_payload,
    extra_env={"GENLAYER_JS_MODULE": GENLAYER_JS_MODULE} if GENLAYER_JS_MODULE else {},
)
if tx_result.returncode != 0:
    sys.exit(f"ERROR: deployment failed\n{tx_result.stderr}")

try:
    output = json.loads(tx_result.stdout.strip())
except json.JSONDecodeError:
    sys.exit(f"ERROR: deployment returned invalid JSON\n{tx_result.stdout}\n{tx_result.stderr}")

address = output.get("contractAddress")
if not address:
    sys.exit(f"ERROR: deployment returned no contract address\n{output}")

print(f"\nContract deployed successfully!")
print(f"Address: {address}")
print(f"Transaction: {output.get('transactionHash')}")
print(f"Status: {output.get('statusName')}")
