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
from genlayer_client import GenLayerCliGateway, GenLayerGatewayError  # noqa: E402
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
        "ALLOWED_ORIGINS": "https://app.example.com",
        "ALLOWED_HOSTS": "api.example.com",
        "API_TOKENS": "t" * 32,
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
        API_TOKENS="token",
    ):
        assert_raises(RuntimeError, load_settings)


def test_production_rejects_wildcard_cors():
    with environment(**production_environment(ALLOWED_ORIGINS="*")):
        assert_raises(RuntimeError, load_settings)


def test_production_rejects_missing_auth_token():
    with environment(**production_environment(API_TOKENS=None)):
        assert_raises(RuntimeError, load_settings)


def test_production_rejects_disabled_write_auth():
    with environment(**production_environment(MEDICHAIN_REQUIRE_WRITE_AUTH="false")):
        assert_raises(RuntimeError, load_settings)


def test_valid_production_settings():
    tokens = ("a" * 32, "b" * 32)
    with environment(**production_environment(API_TOKENS=",".join(tokens))):
        settings = load_settings()
        assert settings.backend_mode == "genlayer"
        assert settings.allowed_origins == ("https://app.example.com",)
        assert settings.allowed_hosts == ("api.example.com",)
        assert settings.api_tokens == tokens


def test_production_rejects_wildcard_host():
    with environment(**production_environment(ALLOWED_HOSTS="*")):
        assert_raises(RuntimeError, load_settings)


def test_production_rejects_short_credentials():
    with environment(**production_environment(API_TOKENS="short")):
        assert_raises(RuntimeError, load_settings)
    with environment(**production_environment(PRIVATE_KEY="not-a-private-key")):
        assert_raises(RuntimeError, load_settings)
    with environment(**production_environment(GENLAYER_KEYSTORE_PASSWORD="short")):
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


def test_genlayer_write_rejects_error_receipt():
    gateway = GenLayerCliGateway("0x1234")
    gateway._ready = True
    gateway._run_process = lambda cmd, stdin=None: "txExecutionResultName: 'FINISHED_WITH_ERROR'"
    assert_raises(IntegrityCheckError, lambda: gateway.write("register_trial", ["ABC"]))


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
        return "ok"

    gateway._run_process = capture
    gateway._ensure_cli_ready()

    all_arguments = " ".join(argument for cmd, _, _ in invocations for argument in cmd)
    assert private_key not in all_arguments
    assert password not in all_arguments
    setup_payload = json.loads(invocations[-1][1])
    assert setup_payload["private_key"] == private_key
    assert setup_payload["password"] == password
    assert "GENLAYER_ETHERS_MODULE" in invocations[-1][2]


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
            API_TOKENS="token",
            GENLAYER_KEYSTORE_PASSWORD="password",
        ):
            gateway._run_process(["genlayer", "network", "set", "testnet-bradbury"])
    finally:
        genlayer_client.subprocess.run = original_run

    assert "PRIVATE_KEY" not in captured
    assert "API_TOKENS" not in captured
    assert "GENLAYER_KEYSTORE_PASSWORD" not in captured


def main() -> int:
    tests = [value for name, value in globals().items() if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} production support tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
