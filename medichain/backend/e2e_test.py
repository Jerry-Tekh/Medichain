"""End-to-end test for MediChain production API.

Reads PRIVATE_KEY and WALLET_ADDRESS from .env in the project root,
then exercises the full authenticated flow against the production backend.
"""

import os
import sys
from pathlib import Path

import requests
from eth_account import Account
from eth_account.messages import encode_defunct

# Load .env from project root (Medichain-main/.env)
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

BASE = os.getenv("API_BASE_URL", "https://medichain-q34c.onrender.com")
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS", "").strip()
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "").strip()

if not WALLET_ADDRESS or not PRIVATE_KEY:
    sys.exit("ERROR: WALLET_ADDRESS and PRIVATE_KEY must be set in .env")

import time
TRIAL_ID = f"E2E-TEST-{time.strftime('%Y%m%d-%H%M%S')}"
REPORT_ID = f"e2e-report-{int(time.time())}"


def request_challenge():
    resp = requests.post(
        f"{BASE}/api/auth/challenge",
        json={"address": WALLET_ADDRESS, "chain_id": 4221},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


def verify_challenge(challenge, signature):
    resp = requests.post(
        f"{BASE}/api/auth/verify",
        json={
            "challenge_id": challenge["challenge_id"],
            "address": WALLET_ADDRESS,
            "signature": signature,
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def auth_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def register_trial(headers):
    payload = {
        "trial_id": TRIAL_ID,
        "clinicaltrials_gov_url": "https://clinicaltrials.gov/study/NCT04280705",
        "primary_hypothesis": "E2E test hypothesis",
        "primary_endpoints": ["Time to Recovery"],
        "expected_sample_size": 100,
        "integrity_bond": 50,
    }
    resp = requests.post(f"{BASE}/api/register_trial", json=payload, headers=headers, timeout=120)
    if not resp.ok:
        raise SystemExit(f"register_trial failed: {resp.status_code} {resp.text}")
    return resp.json()


def submit_results(headers):
    payload = {
        "trial_id": TRIAL_ID,
        "report_id": REPORT_ID,
        "publication_url": "https://example.com",
        "preprint_url": "",
    }
    resp = requests.post(f"{BASE}/api/submit_results", json=payload, headers=headers, timeout=120)
    if not resp.ok:
        print(f"    WARNING: submit_results returned {resp.status_code}: {resp.text[:200]}")
        return None
    return resp.json()


def submit_flag(headers):
    payload = {
        "trial_id": TRIAL_ID,
        "description": "E2E test flag",
        "evidence_url": "",
    }
    resp = requests.post(f"{BASE}/api/submit_flag", json=payload, headers=headers, timeout=120)
    if not resp.ok:
        raise SystemExit(f"submit_flag failed: {resp.status_code} {resp.text}")
    return resp.json()


def get_trials():
    resp = requests.get(f"{BASE}/api/trials", timeout=120)
    resp.raise_for_status()
    return resp.json()


def get_reports(trial_id):
    resp = requests.get(f"{BASE}/api/trial/{trial_id}/reports", timeout=120)
    resp.raise_for_status()
    return resp.json()


def get_flags(trial_id):
    resp = requests.get(f"{BASE}/api/trial/{trial_id}/flags", timeout=120)
    resp.raise_for_status()
    return resp.json()


def main():
    print("=== MediChain Production E2E Test ===")
    print(f"BASE: {BASE}")
    print(f"WALLET: {WALLET_ADDRESS}")
    print()

    # 1. Health
    print("[1] GET /api/ready")
    ready = requests.get(f"{BASE}/api/ready", timeout=120)
    print(f"    status={ready.status_code} body={ready.text[:120]}")
    ready.raise_for_status()

    # 2. Challenge
    print("[2] POST /api/auth/challenge")
    challenge = request_challenge()
    print(f"    challenge_id={challenge['challenge_id']}")

    # 3. Sign + verify
    print("[3] Sign challenge + POST /api/auth/verify")
    message = challenge["message"]
    signed = Account.sign_message(encode_defunct(message.encode()), PRIVATE_KEY)
    token = verify_challenge(challenge, signed.signature.to_0x_hex())
    print(f"    token_prefix={token[:20]}...")

    headers = auth_headers(token)

    # 4. Register trial
    print(f"[4] POST /api/register_trial (trial_id={TRIAL_ID})")
    reg = register_trial(headers)
    print(f"    status={reg.get('status')} trial_id={reg.get('trial_id')}")

    # 5. Submit results
    print(f"[5] POST /api/submit_results (report_id={REPORT_ID})")
    sub = submit_results(headers)
    if sub:
        print(f"    verdict={sub.get('verdict')} confidence={sub.get('confidence')}")

    # 5b. Submit flag
    print("[5b] POST /api/submit_flag")
    flag = submit_flag(headers)
    print(f"    flag_id={flag.get('flag_id')} status={flag.get('status')}")

    # 6. Dashboard / reads
    print("[6] GET /api/trials")
    trials = get_trials()
    print(f"    trial_count={len(trials)} has_our_trial={TRIAL_ID in trials}")

    print(f"[7] GET /api/trial/{TRIAL_ID}/reports")
    reports = get_reports(TRIAL_ID)
    print(f"    report_count={len(reports)}")

    print(f"[8] GET /api/trial/{TRIAL_ID}/flags")
    flags = get_flags(TRIAL_ID)
    print(f"    flag_count={len(flags)}")

    print()
    print("=== E2E Test Complete ===")


if __name__ == "__main__":
    main()
