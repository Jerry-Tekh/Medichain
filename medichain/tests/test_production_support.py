"""No-third-party tests for production support modules.

Run with: python3 medichain/tests/test_production_support.py
"""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "contract"))

from config import load_settings  # noqa: E402
from genlayer_client import (  # noqa: E402
    GenLayerCliGateway,
    GenLayerContractError,
    GenLayerGatewayError,
)
from medichain_contract import IntegrityCheckError  # noqa: E402
from mock_fetcher import mock_webpage_fetcher  # noqa: E402
from mock_llm import mock_llm_client  # noqa: E402
from persistence import PersistentMediChainContract  # noqa: E402


@contextmanager
def environment(**values):
    previous = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def assert_raises(exc_type, callback):
    try:
        callback()
    except exc_type:
        return
    raise AssertionError(f"expected {exc_type.__name__}")


def production_environment(**overrides):
    values = {
        "MEDICHAIN_ENV": "production",
        "MEDICHAIN_BACKEND_MODE": "genlayer",
        "MEDICHAIN_CONTRACT_ADDRESS": "0x" + ("12" * 20),
        "GENLAYER_RPC_URL": "https://rpc-bradbury.genlayer.com",
        "GENLAYER_NETWORK": "testnet-bradbury",
        "GENLAYER_ACCOUNT_NAME": "medichain-production",
        "GENLAYER_MAX_TRANSACTION_COST_WEI": "500000000000000000",
        "ALLOWED_ORIGINS": "https://app.example.com",
        "ALLOWED_HOSTS": "api.example.com",
        "MEDICHAIN_WALLET_AUTH_REQUIRED": "true",
        "DATABASE_URL": "postgresql://user:password@db.example.com:5432/medichain",
        "JWT_SECRET": "j" * 64,
        "JWT_ISSUER": "medichain-api",
        "JWT_AUDIENCE": "medichain-web",
        "MEDICHAIN_AUTH_DOMAIN": "app.example.com",
        "MEDICHAIN_AUTH_URI": "https://app.example.com",
        "MEDICHAIN_AUTH_CHAIN_ID": "4221",
        "MEDICHAIN_ADMIN_WALLETS": "0x1111111111111111111111111111111111111111",
        "MEDICHAIN_REGULATOR_WALLETS": "0x2222222222222222222222222222222222222222",
        "PRIVATE_KEY": "ab" * 32,
        "GENLAYER_KEYSTORE_PASSWORD": "password",
    }
    values.update(overrides)
    return values


def test_production_rejects_local_mode():
    with environment(
        MEDICHAIN_ENV="production",
        MEDICHAIN_BACKEND_MODE="local",
        ALLOWED_ORIGINS="https://app.example.com",
    ):
        assert_raises(RuntimeError, load_settings)


def test_production_rejects_wildcard_cors():
    with environment(**production_environment(ALLOWED_ORIGINS="*")):
        assert_raises(RuntimeError, load_settings)


def test_production_rejects_missing_database():
    with environment(**production_environment(DATABASE_URL=None)):
        assert_raises(RuntimeError, load_settings)


def test_production_rejects_disabled_wallet_auth():
    with environment(**production_environment(MEDICHAIN_WALLET_AUTH_REQUIRED="false")):
        assert_raises(RuntimeError, load_settings)


def test_valid_production_settings():
    with environment(**production_environment()):
        settings = load_settings()
        assert settings.backend_mode == "genlayer"
        assert settings.allowed_origins == ("https://app.example.com",)
        assert settings.allowed_hosts == ("api.example.com",)
        assert settings.auth_chain_id == 4221
        assert settings.admin_wallets == (
            "0x1111111111111111111111111111111111111111",
        )


def test_production_rejects_wildcard_host():
    with environment(**production_environment(ALLOWED_HOSTS="*")):
        assert_raises(RuntimeError, load_settings)


def test_production_rejects_short_credentials():
    with environment(**production_environment(JWT_SECRET="short")):
        assert_raises(RuntimeError, load_settings)
    with environment(**production_environment(PRIVATE_KEY="not-a-private-key")):
        assert_raises(RuntimeError, load_settings)
    with environment(**production_environment(GENLAYER_KEYSTORE_PASSWORD="short")):
        assert_raises(RuntimeError, load_settings)


def test_genlayer_transaction_cost_is_capped_at_half_gen():
    with environment(
        **production_environment(
            GENLAYER_MAX_TRANSACTION_COST_WEI="500000000000000001"
        )
    ):
        assert_raises(RuntimeError, load_settings)
    with environment(
        **production_environment(
            GENLAYER_MAX_TRANSACTION_COST_WEI="500000000000000000"
        )
    ):
        assert (
            load_settings().genlayer_max_transaction_cost_wei
            == 500_000_000_000_000_000
        )


def test_production_rejects_non_postgres_auth_store():
    with environment(**production_environment(DATABASE_URL="sqlite:///auth.db")):
        assert_raises(RuntimeError, load_settings)


def test_production_rejects_wrong_wallet_chain():
    with environment(**production_environment(MEDICHAIN_AUTH_CHAIN_ID="1")):
        assert_raises(RuntimeError, load_settings)


def test_production_rejects_auth_origin_mismatch():
    with environment(
        **production_environment(MEDICHAIN_AUTH_URI="https://other.example.com")
    ):
        assert_raises(RuntimeError, load_settings)


def test_production_requires_admin_wallet():
    with environment(**production_environment(MEDICHAIN_ADMIN_WALLETS=None)):
        assert_raises(RuntimeError, load_settings)


def test_jwt_secret_cannot_reuse_blockchain_private_key():
    key = "ab" * 32
    with environment(**production_environment(PRIVATE_KEY=key, JWT_SECRET=key)):
        assert_raises(RuntimeError, load_settings)


def test_local_state_survives_restart():
    with tempfile.TemporaryDirectory() as tmp:
        state_path = str(Path(tmp) / "state.json")
        first = PersistentMediChainContract(mock_webpage_fetcher, mock_llm_client, state_path)
        first.register_trial(
            "PERSIST-001",
            "https://clinicaltrials.gov/study/CARDIO-204",
            "Drug X reduces mortality",
            ["overall survival at 24 months"],
            2000,
            "0xSponsor",
            100,
        )
        second = PersistentMediChainContract(mock_webpage_fetcher, mock_llm_client, state_path)
        assert second.get_trial("PERSIST-001")["status"] == "active"
        stored = json.loads(Path(state_path).read_text(encoding="utf-8"))
        assert "PERSIST-001" in stored["trials"]
        assert Path(state_path).stat().st_mode & 0o777 == 0o600


def test_genlayer_result_parser():
    gateway = GenLayerCliGateway("0x1234")
    output = """
Result:
{
  trial_id: 'ABC',
  bond: 100n,
  active: true,
  note: null,
  source: 'https://journal.example.org/article',
  summary: "Patient's outcome was stable"
}

Read operation successfully executed
"""
    parsed = gateway._parse_result(output)
    assert parsed == {
        "trial_id": "ABC",
        "bond": 100,
        "active": True,
        "note": None,
        "source": "https://journal.example.org/article",
        "summary": "Patient's outcome was stable",
    }


def test_clinicaltrials_record_becomes_canonical_snapshot():
    gateway = GenLayerCliGateway("0x1234")
    source_url = "https://clinicaltrials.gov/study/NCT04280705"
    snapshot = gateway._protocol_snapshot_from_record(
        {
            "protocolSection": {
                "identificationModule": {
                    "nctId": "NCT04280705",
                    "briefTitle": "Adaptive COVID-19 Treatment Trial",
                },
                "statusModule": {"overallStatus": "COMPLETED"},
                "designModule": {
                    "enrollmentInfo": {"count": 1062},
                },
                "outcomesModule": {
                    "primaryOutcomes": [
                        {"measure": "Time to Recovery"},
                    ],
                },
                "descriptionModule": {
                    "briefSummary": "A randomized treatment trial.",
                },
            },
        },
        source_url,
    )
    assert json.loads(snapshot) == {
        "registry_id": "NCT04280705",
        "official_title": "Adaptive COVID-19 Treatment Trial",
        "overall_status": "COMPLETED",
        "enrollment": 1062,
        "primary_outcomes": ["Time to Recovery"],
        "summary": "A randomized treatment trial.",
        "source_url": source_url,
    }


def test_register_trial_passes_backend_snapshot_to_contract():
    gateway = GenLayerCliGateway("0x1234")
    captured = {}
    gateway._fetch_protocol_snapshot = lambda source_url: '{"registry_id":"NCT04280705"}'
    gateway.write = lambda method, args=None: captured.update(
        {"method": method, "args": args}
    )
    gateway.get_trial = lambda trial_id: {"trial_id": trial_id}

    result = gateway.register_trial(
        "TRIAL-001",
        "https://clinicaltrials.gov/study/NCT04280705",
        "Treatment improves recovery",
        ["Time to Recovery"],
        1062,
        "0x1111111111111111111111111111111111111111",
        100,
    )

    assert result == {"trial_id": "TRIAL-001"}
    assert captured["method"] == "register_trial"
    assert captured["args"][-1] == '{"registry_id":"NCT04280705"}'


def test_genlayer_success_ignores_stderr_diagnostics():
    gateway = GenLayerCliGateway("0x1234")

    class Result:
        stdout = "\nResult:\n{}\n\n"
        stderr = (
            "[genlayer-js] initializeConsensusSmartContract() is deprecated\n"
            "- Calling method list_trials...\n"
            "Read operation successfully executed\n"
        )
        returncode = 0

    import genlayer_client
    original_run = genlayer_client.subprocess.run
    genlayer_client.subprocess.run = lambda *args, **kwargs: Result()
    try:
        output = gateway._run_process(["genlayer", "call"])
    finally:
        genlayer_client.subprocess.run = original_run

    assert output == "Result:\n{}"
    assert gateway._parse_result(output) == {}


def test_genlayer_contract_error_is_classified():
    gateway = GenLayerCliGateway("0x1234")

    class Result:
        stdout = (
            'Error:\nexecution failed: Stderr:"Traceback (most recent call last):'
            "\\nException: unknown trial_id 'MISSING-001'\\n\""
        )
        stderr = "Error during read operation"
        returncode = 1

    import genlayer_client
    original_run = genlayer_client.subprocess.run
    genlayer_client.subprocess.run = lambda *args, **kwargs: Result()
    try:
        try:
            gateway._run_process(["genlayer", "call"])
        except GenLayerContractError as exc:
            assert str(exc) == "unknown trial_id 'MISSING-001'"
        else:
            raise AssertionError("expected GenLayerContractError")
    finally:
        genlayer_client.subprocess.run = original_run


def test_genlayer_transport_error_stays_gateway_error():
    gateway = GenLayerCliGateway("0x1234")

    class Result:
        stdout = ""
        stderr = "connect ECONNREFUSED rpc-bradbury.genlayer.com"
        returncode = 1

    import genlayer_client
    original_run = genlayer_client.subprocess.run
    genlayer_client.subprocess.run = lambda *args, **kwargs: Result()
    try:
        assert_raises(
            GenLayerGatewayError,
            lambda: gateway._run_process(["genlayer", "call"]),
        )
    finally:
        genlayer_client.subprocess.run = original_run


def test_genlayer_read_retries_transient_transport_errors():
    gateway = GenLayerCliGateway("0x1234")
    gateway._ready = True
    attempts = []

    def run_process(_cmd):
        attempts.append(True)
        if len(attempts) < 3:
            raise GenLayerGatewayError("GenLayer CLI failed: fetch failed")
        return "Result:\n{}\n\nRead operation successfully executed"

    import genlayer_client
    original_sleep = genlayer_client.time.sleep
    gateway._run_process = run_process
    genlayer_client.time.sleep = lambda _seconds: None
    try:
        assert gateway.call("list_trials") == {}
    finally:
        genlayer_client.time.sleep = original_sleep

    assert len(attempts) == 3


def test_genlayer_read_does_not_retry_non_transport_errors():
    gateway = GenLayerCliGateway("0x1234")
    gateway._ready = True
    attempts = []

    def run_process(_cmd):
        attempts.append(True)
        raise GenLayerGatewayError("GenLayer CLI failed: invalid request")

    gateway._run_process = run_process
    assert_raises(
        GenLayerGatewayError,
        lambda: gateway.call("list_trials"),
    )
    assert len(attempts) == 1


def test_genlayer_write_rejects_error_receipt():
    gateway = GenLayerCliGateway("0x1234")
    gateway._ready = True
    transaction_hash = "0x" + ("ab" * 32)
    gateway._run_bounded_transaction = lambda *args, **kwargs: {
        "transactionHash": transaction_hash,
        "txExecutionResultName": "FINISHED_WITH_ERROR",
    }
    try:
        gateway.write("register_trial", ["ABC"])
    except IntegrityCheckError as exc:
        assert "register_trial" in str(exc)
        assert transaction_hash in str(exc)
    else:
        raise AssertionError("expected IntegrityCheckError")


def test_genlayer_writes_use_the_bounded_transaction_runner():
    gateway = GenLayerCliGateway(
        "0x1234",
        private_key="ab" * 32,
        max_transaction_cost_wei=500_000_000_000_000_000,
    )
    gateway._ready = True
    captured = {}

    def capture(action, **kwargs):
        captured["action"] = action
        captured.update(kwargs)
        return {
            "transactionHash": "0x" + ("12" * 32),
            "signedCostCeilingWei": "1000",
            "txExecutionResultName": "FINISHED_WITH_RETURN",
        }

    gateway._run_bounded_transaction = capture
    gateway.write("submit_flag", ["trial", "wallet", "description", ""])
    assert captured["action"] == "write"
    assert captured["method"] == "submit_flag"
    assert captured["args"] == ["trial", "wallet", "description", ""]


def test_bounded_transaction_runner_rejects_cost_above_limit():
    fake_module = """
export const chains = { testnetBradbury: {} };
export function createAccount() {
  return {
    address: "0x1111111111111111111111111111111111111111",
    type: "local",
    signTransaction: async () => "0xdead"
  };
}
export function createClient({ account }) {
  return {
    writeContract: async () => {
      await account.signTransaction({ gas: 100n, gasPrice: 2n, value: 0n });
      return "0x" + "12".repeat(32);
    },
    waitForTransactionReceipt: async () => ({
      statusName: "ACCEPTED",
      resultName: "AGREE",
      txExecutionResultName: "FINISHED_WITH_RETURN",
      txDataDecoded: {}
    })
  };
}
"""
    with tempfile.TemporaryDirectory() as tmp:
        module_path = Path(tmp) / "fake-genlayer-js.mjs"
        module_path.write_text(fake_module, encoding="utf-8")
        gateway = GenLayerCliGateway(
            "0x1111111111111111111111111111111111111111",
            rpc_url="https://rpc.example.com",
            private_key="ab" * 32,
            max_transaction_cost_wei=249,
        )
        gateway._ready = True
        with environment(GENLAYER_JS_MODULE=str(module_path)):
            assert_raises(
                GenLayerGatewayError,
                lambda: gateway.write("submit_flag", ["trial", "wallet", "description", ""]),
            )


def test_bounded_transaction_runner_accepts_cost_at_limit():
    fake_module = """
export const chains = { testnetBradbury: {} };
export function createAccount() {
  return {
    address: "0x1111111111111111111111111111111111111111",
    type: "local",
    signTransaction: async () => "0xdead"
  };
}
export function createClient({ account }) {
  return {
    writeContract: async () => {
      await account.signTransaction({ gas: 100n, gasPrice: 2n, value: 0n });
      return "0x" + "12".repeat(32);
    },
    waitForTransactionReceipt: async () => ({
      statusName: "ACCEPTED",
      resultName: "AGREE",
      txExecutionResultName: "FINISHED_WITH_RETURN",
      txDataDecoded: {}
    })
  };
}
"""
    with tempfile.TemporaryDirectory() as tmp:
        module_path = Path(tmp) / "fake-genlayer-js.mjs"
        module_path.write_text(fake_module, encoding="utf-8")
        gateway = GenLayerCliGateway(
            "0x1111111111111111111111111111111111111111",
            rpc_url="https://rpc.example.com",
            private_key="ab" * 32,
            max_transaction_cost_wei=250,
        )
        gateway._ready = True
        with environment(GENLAYER_JS_MODULE=str(module_path)):
            receipt = gateway.write(
                "submit_flag",
                ["trial", "wallet", "description", ""],
            )
        assert receipt["signedCostCeilingWei"] == "250"


def test_bounded_deployment_encodes_address_arguments():
    fake_module = """
export const abi = {
  calldata: {
    decode: (value) => ({ kind: "address", bytes: Array.from(value) })
  }
};
export const chains = { testnetBradbury: {} };
export function createAccount() {
  return {
    address: "0x1111111111111111111111111111111111111111",
    type: "local",
    signTransaction: async () => "0xdead"
  };
}
export function createClient({ account }) {
  return {
    deployContract: async ({ args }) => {
      if (args[0].kind !== "address" || args[0].bytes[0] !== 24) {
        throw new Error("constructor address was not encoded");
      }
      await account.signTransaction({ gas: 100n, gasPrice: 2n, value: 0n });
      return "0x" + "12".repeat(32);
    },
    waitForTransactionReceipt: async () => ({
      statusName: "ACCEPTED",
      resultName: "AGREE",
      txExecutionResultName: "FINISHED_WITH_RETURN",
      txDataDecoded: {
        contractAddress: "0x2222222222222222222222222222222222222222"
      }
    })
  };
}
"""
    with tempfile.TemporaryDirectory() as tmp:
        module_path = Path(tmp) / "fake-genlayer-js.mjs"
        contract_path = Path(tmp) / "contract.py"
        module_path.write_text(fake_module, encoding="utf-8")
        contract_path.write_text("contract", encoding="utf-8")
        gateway = GenLayerCliGateway(
            "0x1111111111111111111111111111111111111111",
            rpc_url="https://rpc.example.com",
            private_key="ab" * 32,
            max_transaction_cost_wei=250,
        )
        gateway._ready = True
        with environment(GENLAYER_JS_MODULE=str(module_path)):
            result = gateway.deploy(
                str(contract_path),
                [{
                    "__medichain_address__":
                    "0x1111111111111111111111111111111111111111"
                }],
            )
        assert result["contractAddress"] == (
            "0x2222222222222222222222222222222222222222"
        )


def test_genlayer_write_rejection_without_hash_stays_readable():
    gateway = GenLayerCliGateway("0x1234")
    message = gateway._write_rejection_message(
        "submit_results",
        "txExecutionResultName: 'FINISHED_WITH_ERROR'",
    )
    assert message == "Bradbury rejected the submit_results contract write"


def test_genlayer_leader_timeout_stays_retryable():
    gateway = GenLayerCliGateway("0x1234")
    assert_raises(
        GenLayerGatewayError,
        lambda: gateway._validate_write_result(
            "register_trial",
            {
                "statusName": "LEADER_TIMEOUT",
                "txExecutionResultName": "FINISHED_WITH_ERROR",
            },
        ),
    )


def test_signer_secrets_are_not_passed_in_process_arguments():
    private_key = "ab" * 32
    password = "keystore-password"
    gateway = GenLayerCliGateway(
        "0x1234",
        private_key=private_key,
        keystore_password=password,
    )
    invocations = []

    def capture(cmd, stdin=None, extra_env=None):
        invocations.append((cmd, stdin, extra_env))
        return "GenLayer signer ready: 0x1111111111111111111111111111111111111111"

    gateway._run_process = capture
    gateway._ensure_cli_ready()

    all_arguments = " ".join(argument for cmd, _, _ in invocations for argument in cmd)
    assert private_key not in all_arguments
    assert password not in all_arguments
    setup_payload = json.loads(invocations[-1][1])
    assert setup_payload["private_key"] == private_key
    assert setup_payload["password"] == password
    assert "GENLAYER_ETHERS_MODULE" in invocations[-1][2]
    assert gateway.signer_address == "0x1111111111111111111111111111111111111111"


def test_npx_cli_resolves_its_cached_ethers_module():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        package_root = home / ".npm" / "_npx" / "cache-key" / "node_modules"
        cli_entry = package_root / "genlayer" / "dist" / "index.js"
        ethers_entry = package_root / "ethers" / "lib.esm" / "index.js"
        genlayer_js_entry = package_root / "genlayer-js" / "dist" / "index.js"
        binary = package_root / ".bin" / "genlayer"
        cli_entry.parent.mkdir(parents=True)
        ethers_entry.parent.mkdir(parents=True)
        genlayer_js_entry.parent.mkdir(parents=True)
        binary.parent.mkdir(parents=True)
        cli_entry.write_text("", encoding="utf-8")
        ethers_entry.write_text("", encoding="utf-8")
        genlayer_js_entry.write_text("", encoding="utf-8")
        binary.symlink_to(cli_entry)

        with environment(HOME=str(home)):
            gateway = GenLayerCliGateway(
                "0x1234",
                cli_command="npx -y genlayer@0.39.2",
            )
            assert gateway._ethers_module_path() == str(ethers_entry)
            assert gateway._genlayer_js_module_path() == str(genlayer_js_entry)


def test_cli_subprocess_environment_excludes_application_secrets():
    gateway = GenLayerCliGateway("0x1234")
    captured = {}

    class Result:
        stdout = "ok"
        stderr = ""
        returncode = 0

    original_run = __import__("subprocess").run

    def capture_run(cmd, **kwargs):
        captured.update(kwargs["env"])
        return Result()

    import genlayer_client
    genlayer_client.subprocess.run = capture_run
    try:
        with environment(
            PRIVATE_KEY="private",
            GENLAYER_KEYSTORE_PASSWORD="password",
        ):
            gateway._run_process(["genlayer", "network", "set", "testnet-bradbury"])
    finally:
        genlayer_client.subprocess.run = original_run

    assert "PRIVATE_KEY" not in captured
    assert "GENLAYER_KEYSTORE_PASSWORD" not in captured


def test_streamed_cli_output_is_returned_and_persisted():
    gateway = GenLayerCliGateway("0x1234")
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "deploy.log"
        output = gateway._run_process_streamed(
            [
                sys.executable,
                "-c",
                (
                    "import sys;"
                    "print('Contract Address: 0x' + ('12' * 20), flush=True);"
                    "print('AGREE', file=sys.stderr, flush=True)"
                ),
            ],
            output_log=log_path,
        )

        persisted = log_path.read_text(encoding="utf-8")
        assert "Contract Address: 0x" in output
        assert "Contract Address: 0x" in persisted
        assert "AGREE" in persisted


def main() -> int:
    tests = [value for name, value in globals().items() if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} production support tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
