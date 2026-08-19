#!/usr/bin/env python3
"""Securely deploy and verify the MediChain adapter on Bradbury."""

import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
from datetime import datetime, timezone


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "contract"))

from config import (  # noqa: E402
    ADDRESS_PATTERN,
    MAX_GENLAYER_TRANSACTION_COST_WEI,
    PRIVATE_KEY_PATTERN,
)
from genlayer_client import GenLayerCliGateway  # noqa: E402


def required_environment(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def deployment_log_path() -> Path:
    configured = os.getenv("MEDICHAIN_DEPLOY_LOG", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return ROOT / ".deploy" / f"bradbury-{timestamp}.log"


def main() -> int:
    private_key = required_environment("PRIVATE_KEY")
    treasury_address = required_environment("TREASURE_ADDRESS")
    if not PRIVATE_KEY_PATTERN.fullmatch(private_key):
        raise RuntimeError("PRIVATE_KEY must be a 32-byte hex key")
    if not ADDRESS_PATTERN.fullmatch(treasury_address):
        raise RuntimeError("TREASURE_ADDRESS must be a 20-byte hex address")

    network = os.getenv("GENLAYER_NETWORK", "testnet-bradbury").strip()
    rpc_url = os.getenv(
        "GENLAYER_RPC_URL",
        "https://rpc-bradbury.genlayer.com",
    ).strip()
    cli_command = os.getenv("GENLAYER_CLI_COMMAND", "genlayer").strip()
    max_transaction_cost_wei = int(os.getenv(
        "GENLAYER_MAX_TRANSACTION_COST_WEI",
        str(MAX_GENLAYER_TRANSACTION_COST_WEI),
    ))
    if not (
        0
        < max_transaction_cost_wei
        <= MAX_GENLAYER_TRANSACTION_COST_WEI
    ):
        raise RuntimeError(
            "GENLAYER_MAX_TRANSACTION_COST_WEI must be positive and "
            "no greater than 0.5 GEN"
        )
    password = os.getenv("GENLAYER_KEYSTORE_PASSWORD") or secrets.token_hex(32)
    contract_path = ROOT / "contract" / "genlayer_adapter.py"
    deploy_log = deployment_log_path()
    print(f"Deployment progress log: {deploy_log}", flush=True)

    original_home = os.environ.get("HOME")
    original_ethers_module = os.environ.get("GENLAYER_ETHERS_MODULE")
    original_genlayer_js_module = os.environ.get("GENLAYER_JS_MODULE")
    resolver = GenLayerCliGateway(
        contract_address="0x" + ("00" * 20),
        cli_command=cli_command,
    )
    if not original_ethers_module:
        os.environ["GENLAYER_ETHERS_MODULE"] = resolver._ethers_module_path()
    if not original_genlayer_js_module:
        os.environ["GENLAYER_JS_MODULE"] = resolver._genlayer_js_module_path()
    try:
        with tempfile.TemporaryDirectory(prefix="medichain-deploy-") as temporary_home:
            os.environ["HOME"] = temporary_home
            gateway = GenLayerCliGateway(
                contract_address="0x" + ("00" * 20),
                rpc_url=rpc_url,
                network=network,
                account_name="medichain-deployer",
                private_key=private_key,
                cli_command=cli_command,
                max_transaction_cost_wei=max_transaction_cost_wei,
                keystore_password=password,
                timeout_seconds=600,
            )
            gateway._ensure_cli_ready()
            deploy_result = gateway.deploy(
                str(contract_path),
                [{"__medichain_address__": treasury_address}],
                output_log=deploy_log,
            )
            with deploy_log.open("a", encoding="utf-8") as log_file:
                log_file.write("\nVerified deployment receipt:\n")
                log_file.write(json.dumps(deploy_result, indent=2) + "\n")
            if (
                deploy_result.get("txExecutionResultName") != "FINISHED_WITH_RETURN"
                or deploy_result.get("resultName") != "AGREE"
            ):
                raise RuntimeError(
                    "deployment did not report AGREE and FINISHED_WITH_RETURN"
                )

            contract_address = deploy_result["contractAddress"]
            gateway.contract_address = contract_address
            schema_output = gateway._run_process([
                *gateway.cli_command,
                "schema",
                contract_address,
                "--rpc",
                rpc_url,
            ])
            for method in (
                "register_trial",
                "submit_results",
                "get_treasury_address",
                "get_owner",
            ):
                if method not in schema_output:
                    raise RuntimeError(f"deployed schema is missing {method}")
            treasury_result = gateway.call("get_treasury_address")
            owner_result = gateway.call("get_owner")
            if not owner_result:
                raise RuntimeError("deployed contract returned no relayer owner")
    finally:
        if original_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = original_home
        if original_ethers_module is None:
            os.environ.pop("GENLAYER_ETHERS_MODULE", None)
        else:
            os.environ["GENLAYER_ETHERS_MODULE"] = original_ethers_module
        if original_genlayer_js_module is None:
            os.environ.pop("GENLAYER_JS_MODULE", None)
        else:
            os.environ["GENLAYER_JS_MODULE"] = original_genlayer_js_module

    print(json.dumps({
        "network": network,
        "contract_address": contract_address,
        "treasury_address": treasury_result,
        "owner_address": owner_result,
        "signed_cost_ceiling_wei": deploy_result["signedCostCeilingWei"],
        "schema_verified": True,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
