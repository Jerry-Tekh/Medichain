"""GenLayer CLI-backed MediChain gateway.

The Python API has no direct py-genlayer runtime outside GenVM, so production
mode delegates contract reads/writes to the GenLayer CLI and normalizes the
responses back into the JSON shape expected by the frontend.
"""

import ast
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import shutil
import subprocess
import sys
import threading
import time

from medichain_contract import IntegrityCheckError


class GenLayerGatewayError(RuntimeError):
    """Raised when the Bradbury CLI or network is unavailable."""


class GenLayerContractError(IntegrityCheckError):
    """Raised when GenVM intentionally rejects a contract operation."""


class GenLayerCliGateway:
    def __init__(
        self,
        contract_address: str,
        rpc_url: str = "",
        network: str = "testnet-bradbury",
        account_name: str = "medichain-production",
        private_key: str = "",
        cli_command: str = "genlayer",
        fees: str = "",
        keystore_password: str = "",
        timeout_seconds: int = 600,
    ):
        self.contract_address = contract_address
        self.rpc_url = rpc_url
        self.network = network
        self.account_name = account_name
        self.private_key = private_key
        self.cli_command = tuple(shlex.split(cli_command))
        self.fees = fees
        self.keystore_password = keystore_password
        self.timeout_seconds = timeout_seconds
        self._ready = False
        self._ready_lock = threading.Lock()
        self._write_lock = threading.RLock()

    def _arg(self, value):
        if isinstance(value, bool):
            return "true" if value else "false"
        if value is None:
            return "null"
        if isinstance(value, (list, dict)):
            return json.dumps(value)
        return str(value)

    def _process_environment(self, extra_env=None):
        process_env = os.environ.copy()
        for secret_name in ("PRIVATE_KEY", "GENLAYER_KEYSTORE_PASSWORD"):
            process_env.pop(secret_name, None)
        process_env.update({"NO_COLOR": "1", "FORCE_COLOR": "0", "NO_UPDATE_NOTIFIER": "1"})
        process_env.update(extra_env or {})
        return process_env

    def _raise_process_error(self, stdout: str, stderr: str):
        diagnostics = f"{stdout}\n{stderr}".strip()
        contract_error = self._extract_contract_error(diagnostics)
        if contract_error:
            raise GenLayerContractError(contract_error)
        raise GenLayerGatewayError(f"GenLayer CLI failed: {diagnostics[-1200:]}")

    def _run_process(self, cmd, stdin=None, extra_env=None):
        try:
            result = subprocess.run(
                cmd,
                input=stdin,
                env=self._process_environment(extra_env),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise GenLayerGatewayError(
                f"GenLayer CLI timed out after {self.timeout_seconds} seconds"
            ) from exc
        if result.returncode != 0:
            self._raise_process_error(result.stdout, result.stderr)

        # genlayer-cli writes the contract result to stdout and progress,
        # warnings, and spinner status to stderr. Parsing the combined streams
        # corrupts otherwise valid JSON/object results.
        return result.stdout.strip() or result.stderr.strip()

    def _run_process_streamed(self, cmd, stdin=None, extra_env=None, output_log=None):
        """Run a long CLI operation while durably teeing its public output."""
        log_path = Path(output_log) if output_log else None
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)

        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self._process_environment(extra_env),
        )
        if stdin is not None:
            process.stdin.write(stdin.encode("utf-8"))
            process.stdin.close()

        stdout_chunks = []
        stderr_chunks = []
        started_at = time.monotonic()
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        log_file = log_path.open("wb", buffering=0) if log_path else None

        try:
            while selector.get_map():
                remaining = self.timeout_seconds - (time.monotonic() - started_at)
                if remaining <= 0:
                    process.kill()
                    process.wait()
                    raise GenLayerGatewayError(
                        f"GenLayer CLI timed out after {self.timeout_seconds} seconds"
                    )
                for key, _ in selector.select(timeout=min(1, remaining)):
                    chunk = os.read(key.fileobj.fileno(), 4096)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    if key.data == "stdout":
                        stdout_chunks.append(chunk)
                        target = getattr(sys.stdout, "buffer", sys.stdout)
                    else:
                        stderr_chunks.append(chunk)
                        target = getattr(sys.stderr, "buffer", sys.stderr)
                    target.write(chunk)
                    target.flush()
                    if log_file:
                        log_file.write(chunk)
                        os.fsync(log_file.fileno())
            returncode = process.wait()
        except BaseException:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            raise
        finally:
            selector.close()
            if log_file:
                log_file.close()

        stdout = b"".join(stdout_chunks).decode("utf-8", errors="replace")
        stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace")
        if returncode != 0:
            self._raise_process_error(stdout, stderr)
        return stdout.strip() or stderr.strip()

    def _extract_contract_error(self, output: str) -> str:
        normalized = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)
        normalized = normalized.replace("\\n", "\n").replace('\\"', '"')
        matches = re.findall(
            r"(?:Exception|AssertionError|ValueError|RuntimeError):[ \t]*([^\n\"]+)",
            normalized,
        )
        if not matches:
            return ""
        return matches[-1].strip()

    def _ethers_module_path(self) -> str:
        override = os.getenv("GENLAYER_ETHERS_MODULE")
        if override:
            return override

        candidates = []
        executable = shutil.which(self.cli_command[0])
        if executable:
            candidates.append(Path(executable).resolve())
        candidates.extend(
            Path(argument).resolve()
            for argument in self.cli_command[1:]
            if argument.endswith((".js", ".mjs", ".cjs"))
        )
        npm_cache = Path.home() / ".npm" / "_npx"
        if npm_cache.is_dir():
            candidates.extend(
                executable.resolve()
                for executable in npm_cache.glob("*/node_modules/.bin/genlayer")
            )
        for candidate in candidates:
            if candidate.parent.name != "dist":
                continue
            package_root = candidate.parent.parent
            for ethers_module in (
                package_root / "node_modules" / "ethers" / "lib.esm" / "index.js",
                package_root.parent / "ethers" / "lib.esm" / "index.js",
            ):
                if ethers_module.is_file():
                    return str(ethers_module)
        return "/usr/local/lib/node_modules/genlayer/node_modules/ethers/lib.esm/index.js"

    def _ensure_cli_ready(self) -> None:
        if self._ready:
            return
        with self._ready_lock:
            if self._ready:
                return
            if self.private_key:
                if not self.keystore_password:
                    raise GenLayerGatewayError("GENLAYER_KEYSTORE_PASSWORD is required to import the signer")
                setup_script = Path(__file__).with_name("setup_genlayer_account.mjs")
                self._run_process(
                    ["node", str(setup_script)],
                    json.dumps({
                        "private_key": self.private_key,
                        "password": self.keystore_password,
                        "account_name": self.account_name,
                        "network": self.network,
                    }),
                    {"GENLAYER_ETHERS_MODULE": self._ethers_module_path()},
                )
            else:
                if self.network:
                    self._run_process([*self.cli_command, "network", "set", self.network])
                self._run_process([*self.cli_command, "account", "use", self.account_name])
            self._ready = True

    def _run(self, action: str, method: str, args=None):
        args = args or []
        self._ensure_cli_ready()

        cmd = [*self.cli_command, action, self.contract_address, method]
        if self.rpc_url:
            cmd.extend(["--rpc", self.rpc_url])
        if action == "write" and self.fees:
            cmd.extend(["--fees", self.fees])
        if args:
            cmd.append("--args")
            cmd.extend(self._arg(item) for item in args)

        stdin = self.keystore_password + "\n" if action == "write" else None
        try:
            output = self._run_process(cmd, stdin)
        except GenLayerGatewayError as exc:
            raise GenLayerGatewayError(f"GenLayer {action} failed for {method}: {exc}") from exc
        if action == "write":
            if "FINISHED_WITH_ERROR" in output:
                raise IntegrityCheckError(
                    f"Bradbury rejected the {method} contract write"
                )
            if "LEADER_TIMEOUT" in output:
                raise GenLayerGatewayError(f"GenLayer write failed for {method}: LEADER_TIMEOUT")
            if "FINISHED_WITH_RETURN" not in output:
                raise GenLayerGatewayError(
                    f"GenLayer write for {method} did not return a successful execution receipt"
                )
        return output

    def _extract_result_text(self, output: str) -> str:
        output = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)
        match = re.search(
            r"(?:^|\n)Result:\n(?P<result>.*?)(?:\n\n[^\n]*successfully|$)",
            output,
            re.S,
        )
        if not match:
            return output.strip()
        return match.group("result").strip()

    def _parse_result(self, output: str):
        text = self._extract_result_text(output)
        if text in {"", "null", "None"}:
            return None
        if re.fullmatch(r"0x[0-9a-fA-F]+", text):
            return text

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        pythonish = self._pythonize_inspect(text)
        try:
            return ast.literal_eval(pythonish)
        except (SyntaxError, ValueError):
            return {"raw": text}

    def _pythonize_inspect(self, text: str) -> str:
        """Convert Node's util.inspect object syntax without touching strings."""
        result = []
        index = 0
        length = len(text)
        while index < length:
            character = text[index]
            if character in {"'", '"'}:
                quote = character
                start = index
                index += 1
                while index < length:
                    if text[index] == "\\":
                        index += 2
                        continue
                    if text[index] == quote:
                        index += 1
                        break
                    index += 1
                result.append(text[start:index])
                continue

            if character.isalpha() or character == "_":
                start = index
                index += 1
                while index < length and (text[index].isalnum() or text[index] == "_"):
                    index += 1
                word = text[start:index]
                lookahead = index
                while lookahead < length and text[lookahead].isspace():
                    lookahead += 1
                if lookahead < length and text[lookahead] == ":":
                    result.append(repr(word))
                else:
                    result.append({
                        "true": "True",
                        "false": "False",
                        "null": "None",
                        "undefined": "None",
                    }.get(word, word))
                continue

            if character.isdigit():
                start = index
                index += 1
                while index < length and (text[index].isdigit() or text[index] in ".eE+-"):
                    index += 1
                result.append(text[start:index])
                if index < length and text[index] == "n":
                    index += 1
                continue

            result.append(character)
            index += 1
        return "".join(result)

    def call(self, method: str, args=None):
        return self._parse_result(self._run("call", method, args))

    def write(self, method: str, args=None):
        with self._write_lock:
            return self._parse_result(self._run("write", method, args))

    def register_trial(
        self,
        trial_id,
        clinicaltrials_gov_url,
        primary_hypothesis,
        primary_endpoints,
        expected_sample_size,
        sponsor_wallet,
        integrity_bond,
    ):
        with self._write_lock:
            self.write("register_trial", [
                trial_id,
                clinicaltrials_gov_url,
                primary_hypothesis,
                primary_endpoints,
                expected_sample_size,
                sponsor_wallet,
                integrity_bond,
            ])
            return self.get_trial(trial_id)

    def submit_results(self, trial_id, report_id, publication_url, preprint_url=""):
        with self._write_lock:
            self.write("submit_results", [trial_id, report_id, publication_url, preprint_url or ""])
            return self.get_report(report_id)

    def resolve_appeal(self, trial_id, decision, resolver):
        with self._write_lock:
            self.write("resolve_appeal", [trial_id, decision, resolver])
            return self.get_trial(trial_id)

    def submit_flag(self, trial_id, submitter, description, evidence_url=""):
        with self._write_lock:
            before = set(self.list_flags_for_trial(trial_id))
            self.write("submit_flag", [trial_id, submitter, description, evidence_url or ""])
            flags = self.list_flags_for_trial(trial_id)
            new_ids = set(flags) - before
            matching_ids = [
                flag_id for flag_id in new_ids
                if flags[flag_id].get("submitter") == submitter
                and flags[flag_id].get("description") == description
                and flags[flag_id].get("evidence_url", "") == (evidence_url or "")
            ]
            if len(matching_ids) != 1:
                raise GenLayerGatewayError(
                    "GenLayer submit_flag completed but its resulting flag could not be identified"
                )
            flag_id = matching_ids[0]
            return flag_id, flags[flag_id]

    def get_trial(self, trial_id):
        return self.call("get_trial", [trial_id])

    def get_report(self, report_id):
        return self.call("get_report", [report_id])

    def list_trials(self):
        return self.call("list_trials") or {}

    def list_reports_for_trial(self, trial_id):
        return self.call("list_reports_for_trial", [trial_id]) or {}

    def list_flags_for_trial(self, trial_id):
        return self.call("list_flags_for_trial", [trial_id]) or {}
