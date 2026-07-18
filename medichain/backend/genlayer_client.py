"""GenLayer CLI-backed MediChain gateway.

The Python API has no direct py-genlayer runtime outside GenVM, so production
mode delegates contract reads/writes to the GenLayer CLI and normalizes the
responses back into the JSON shape expected by the frontend.
"""

import ast
from html.parser import HTMLParser
import ipaddress
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from medichain_contract import IntegrityCheckError


READ_RETRY_ATTEMPTS = 3
READ_RETRY_BASE_DELAY_SECONDS = 1
RETRYABLE_TRANSPORT_MARKERS = (
    "fetch failed",
    "timed out",
    "timeout",
    "etimedout",
    "enetunreach",
    "econnrefused",
    "econnreset",
    "socket hang up",
    "network error",
    "temporarily unavailable",
    "bad gateway",
    "service unavailable",
    "gateway timeout",
)
DOCUMENT_RESPONSE_LIMIT_BYTES = 2_000_000
DOCUMENT_SNAPSHOT_LIMIT_CHARS = 8_000
ALLOWED_DOCUMENT_CONTENT_TYPES = frozenset({
    "application/json",
    "application/ld+json",
    "application/xhtml+xml",
    "application/xml",
    "text/html",
    "text/plain",
    "text/xml",
})


class _DocumentTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self._focus_depth = 0
        self._all_parts = []
        self._focused_parts = []

    def handle_starttag(self, tag, attrs):
        normalized = tag.lower()
        if normalized in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1
            return
        if normalized in {"main", "article"}:
            self._focus_depth += 1

    def handle_endtag(self, tag):
        normalized = tag.lower()
        if normalized in {"script", "style", "noscript", "svg"}:
            self._ignored_depth = max(0, self._ignored_depth - 1)
            return
        if normalized in {"main", "article"} and self._focus_depth:
            self._focus_depth -= 1

    def handle_data(self, data):
        if self._ignored_depth:
            return
        text = data.strip()
        if not text:
            return
        self._all_parts.append(text)
        if self._focus_depth:
            self._focused_parts.append(text)

    def text(self) -> str:
        focused = " ".join(self._focused_parts)
        return focused if focused else " ".join(self._all_parts)


class _ValidatingRedirectHandler(HTTPRedirectHandler):
    def __init__(self, validator):
        super().__init__()
        self._validator = validator

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self._validator(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


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
        max_transaction_cost_wei: int = 500_000_000_000_000_000,
        keystore_password: str = "",
        timeout_seconds: int = 600,
    ):
        self.contract_address = contract_address
        self.rpc_url = rpc_url
        self.network = network
        self.account_name = account_name
        self.private_key = private_key
        self.cli_command = tuple(shlex.split(cli_command))
        self.max_transaction_cost_wei = max_transaction_cost_wei
        self.keystore_password = keystore_password
        self.timeout_seconds = timeout_seconds
        self.signer_address = ""
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

    def _clinicaltrials_api_url(self, source_url: str) -> str:
        parsed = urlparse(source_url)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if (
            parsed.scheme != "https"
            or hostname not in {"clinicaltrials.gov", "www.clinicaltrials.gov"}
        ):
            raise GenLayerGatewayError(
                "clinical trial source must be an HTTPS ClinicalTrials.gov URL"
            )
        registry_match = re.search(r"NCT[0-9]{8}", source_url.upper())
        if not registry_match:
            raise GenLayerGatewayError(
                "ClinicalTrials.gov URL must contain a valid NCT identifier"
            )
        return (
            "https://clinicaltrials.gov/api/v2/studies/"
            f"{registry_match.group(0)}"
        )

    def _protocol_snapshot_from_record(self, record: dict, source_url: str) -> str:
        try:
            protocol = record["protocolSection"]
            identification = protocol["identificationModule"]
            status = protocol.get("statusModule", {})
            design = protocol.get("designModule", {})
            outcomes_module = protocol["outcomesModule"]
        except (KeyError, TypeError) as exc:
            raise GenLayerGatewayError(
                "ClinicalTrials.gov returned an incomplete protocol record"
            ) from exc

        registry_id = str(identification.get("nctId", "")).strip().upper()
        expected_registry_id = self._clinicaltrials_api_url(source_url).rsplit("/", 1)[-1]
        official_title = str(
            identification.get("officialTitle")
            or identification.get("briefTitle")
            or ""
        ).strip()
        enrollment_info = design.get("enrollmentInfo", {})
        primary_outcomes = outcomes_module.get("primaryOutcomes", [])
        if registry_id != expected_registry_id or not official_title:
            raise GenLayerGatewayError(
                "ClinicalTrials.gov record does not match the requested study"
            )
        if not isinstance(enrollment_info, dict):
            enrollment_info = {}
        try:
            enrollment = int(enrollment_info.get("count", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise GenLayerGatewayError(
                "ClinicalTrials.gov returned an invalid enrollment count"
            ) from exc
        if enrollment < 0 or not isinstance(primary_outcomes, list):
            raise GenLayerGatewayError(
                "ClinicalTrials.gov returned malformed protocol values"
            )

        outcomes = []
        for item in primary_outcomes:
            measure = item.get("measure", "") if isinstance(item, dict) else item
            text = str(measure).strip()
            if text:
                outcomes.append(text[:500])
        if not outcomes:
            raise GenLayerGatewayError(
                "ClinicalTrials.gov record contains no primary outcomes"
            )

        description = protocol.get("descriptionModule", {})
        if not isinstance(description, dict):
            description = {}
        snapshot = {
            "registry_id": registry_id,
            "official_title": official_title[:1000],
            "overall_status": str(
                status.get("overallStatus", "unknown")
                if isinstance(status, dict)
                else "unknown"
            ).strip()[:128],
            "enrollment": enrollment,
            "primary_outcomes": outcomes[:20],
            "summary": str(description.get("briefSummary", "")).strip()[:1500],
            "source_url": source_url,
        }
        return json.dumps(snapshot, sort_keys=True, separators=(",", ":"))

    def _fetch_protocol_snapshot(self, source_url: str) -> str:
        request = Request(
            self._clinicaltrials_api_url(source_url),
            headers={
                "Accept": "application/json",
                "User-Agent": "MediChain/2.0",
            },
        )
        try:
            with urlopen(
                request,
                timeout=min(self.timeout_seconds, 30),
            ) as response:
                body = response.read(5_000_001)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise GenLayerGatewayError(
                "ClinicalTrials.gov protocol fetch failed"
            ) from exc
        if len(body) > 5_000_000:
            raise GenLayerGatewayError(
                "ClinicalTrials.gov protocol response exceeded 5 MB"
            )
        try:
            record = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GenLayerGatewayError(
                "ClinicalTrials.gov returned invalid JSON"
            ) from exc
        if not isinstance(record, dict):
            raise GenLayerGatewayError(
                "ClinicalTrials.gov returned an invalid protocol record"
            )
        return self._protocol_snapshot_from_record(record, source_url)

    def _validate_public_https_url(self, source_url: str) -> str:
        parsed = urlparse(source_url)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if (
            parsed.scheme != "https"
            or not hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
            or hostname == "localhost"
            or hostname.endswith(".localhost")
        ):
            raise GenLayerGatewayError(
                "document source must be a public HTTPS URL"
            )
        try:
            addresses = {
                item[4][0]
                for item in socket.getaddrinfo(
                    hostname,
                    parsed.port or 443,
                    type=socket.SOCK_STREAM,
                )
            }
        except OSError as exc:
            raise GenLayerGatewayError(
                "document source hostname could not be resolved"
            ) from exc
        if not addresses or any(
            not ipaddress.ip_address(address).is_global
            for address in addresses
        ):
            raise GenLayerGatewayError(
                "document source resolved to a private or non-global address"
            )
        return source_url

    def _document_snapshot_from_body(
        self,
        source_url: str,
        resolved_url: str,
        content_type: str,
        body: bytes,
        charset: str = "utf-8",
    ) -> str:
        if content_type not in ALLOWED_DOCUMENT_CONTENT_TYPES:
            raise GenLayerGatewayError(
                f"document source returned unsupported content type {content_type}"
            )
        try:
            decoded = body.decode(charset or "utf-8", errors="replace")
        except LookupError:
            decoded = body.decode("utf-8", errors="replace")
        if content_type in {"text/html", "application/xhtml+xml"}:
            extractor = _DocumentTextExtractor()
            extractor.feed(decoded)
            decoded = extractor.text()
        text = re.sub(r"\s+", " ", decoded).strip()
        if not text:
            raise GenLayerGatewayError(
                "document source returned no readable text"
            )
        return json.dumps({
            "source_url": source_url,
            "resolved_url": resolved_url,
            "content_type": content_type,
            "text": text[:DOCUMENT_SNAPSHOT_LIMIT_CHARS],
        }, sort_keys=True, separators=(",", ":"))

    def _fetch_document_snapshot(self, source_url: str) -> str:
        self._validate_public_https_url(source_url)
        opener = build_opener(
            _ValidatingRedirectHandler(self._validate_public_https_url)
        )
        request = Request(
            source_url,
            headers={
                "Accept": (
                    "text/html,text/plain,application/xhtml+xml,"
                    "application/json,application/xml;q=0.9"
                ),
                "User-Agent": "MediChain/2.0",
            },
        )
        try:
            with opener.open(
                request,
                timeout=min(self.timeout_seconds, 30),
            ) as response:
                resolved_url = self._validate_public_https_url(
                    response.geturl()
                )
                content_type = response.headers.get_content_type().lower()
                charset = response.headers.get_content_charset() or "utf-8"
                body = response.read(DOCUMENT_RESPONSE_LIMIT_BYTES + 1)
        except GenLayerGatewayError:
            raise
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise GenLayerGatewayError(
                "document source fetch failed"
            ) from exc
        if len(body) > DOCUMENT_RESPONSE_LIMIT_BYTES:
            raise GenLayerGatewayError(
                "document source response exceeded 2 MB"
            )
        return self._document_snapshot_from_body(
            source_url,
            resolved_url,
            content_type,
            body,
            charset,
        )

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

    def _write_rejection_message(self, method: str, output: str) -> str:
        transaction_match = re.search(
            r"(?:Write Transaction Hash:\s*|txId:\s*['\"]|"
            r"\"transactionHash\"\s*:\s*\")(0x[0-9a-fA-F]{64})",
            output,
        )
        message = f"Bradbury rejected the {method} contract write"
        if transaction_match:
            message += f" (transaction {transaction_match.group(1)})"
        return message

    def _cli_package_roots(self) -> list[Path]:
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
        package_roots = []
        for candidate in candidates:
            if candidate.parent.name == "dist":
                package_roots.append(candidate.parent.parent)
        return package_roots

    def _ethers_module_path(self) -> str:
        override = os.getenv("GENLAYER_ETHERS_MODULE")
        if override:
            return override

        for package_root in self._cli_package_roots():
            for ethers_module in (
                package_root / "node_modules" / "ethers" / "lib.esm" / "index.js",
                package_root.parent / "ethers" / "lib.esm" / "index.js",
            ):
                if ethers_module.is_file():
                    return str(ethers_module)
        return "/usr/local/lib/node_modules/genlayer/node_modules/ethers/lib.esm/index.js"

    def _genlayer_js_module_path(self) -> str:
        override = os.getenv("GENLAYER_JS_MODULE")
        if override:
            return override

        for package_root in self._cli_package_roots():
            for module in (
                package_root / "node_modules" / "genlayer-js" / "dist" / "index.js",
                package_root.parent / "genlayer-js" / "dist" / "index.js",
            ):
                if module.is_file():
                    return str(module)
        return "/usr/local/lib/node_modules/genlayer/node_modules/genlayer-js/dist/index.js"

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
                setup_output = self._run_process(
                    ["node", str(setup_script)],
                    json.dumps({
                        "private_key": self.private_key,
                        "password": self.keystore_password,
                        "account_name": self.account_name,
                        "network": self.network,
                    }),
                    {"GENLAYER_ETHERS_MODULE": self._ethers_module_path()},
                )
                signer_match = re.search(
                    r"GenLayer signer ready:\s*(0x[0-9a-fA-F]{40})",
                    setup_output,
                )
                if not signer_match:
                    raise GenLayerGatewayError(
                        "GenLayer signer setup returned no wallet address"
                    )
                self.signer_address = signer_match.group(1).lower()
            else:
                if self.network:
                    self._run_process([*self.cli_command, "network", "set", self.network])
                self._run_process([*self.cli_command, "account", "use", self.account_name])
            self._ready = True

    def _tag_transaction_value(self, value):
        if isinstance(value, bool) or value is None or isinstance(value, str):
            return value
        if isinstance(value, int):
            return {"__medichain_int__": str(value)}
        if isinstance(value, list):
            return [self._tag_transaction_value(item) for item in value]
        if isinstance(value, dict):
            return {
                key: self._tag_transaction_value(item)
                for key, item in value.items()
            }
        raise GenLayerGatewayError(
            f"unsupported GenLayer transaction argument type: {type(value).__name__}"
        )

    def _run_bounded_transaction(
        self,
        action: str,
        *,
        method: str = "",
        args=None,
        contract_path: str = "",
    ) -> dict:
        if not self.private_key:
            raise GenLayerGatewayError(
                "PRIVATE_KEY is required for bounded GenLayer transactions"
            )
        payload = {
            "action": action,
            "private_key": self.private_key,
            "rpc_url": self.rpc_url,
            "network": self.network,
            "max_transaction_cost_wei": str(self.max_transaction_cost_wei),
            "args": self._tag_transaction_value(args or []),
        }
        if action == "write":
            payload.update({
                "contract_address": self.contract_address,
                "method": method,
            })
        elif action == "deploy":
            payload["contract_path"] = contract_path
        else:
            raise GenLayerGatewayError(f"unsupported transaction action: {action}")

        script = Path(__file__).with_name("genlayer_transaction.mjs")
        output = self._run_process(
            ["node", str(script)],
            json.dumps(payload),
            {"GENLAYER_JS_MODULE": self._genlayer_js_module_path()},
        )
        try:
            result = json.loads(output)
        except json.JSONDecodeError as exc:
            raise GenLayerGatewayError(
                "bounded GenLayer transaction returned invalid JSON"
            ) from exc
        if not isinstance(result, dict):
            raise GenLayerGatewayError(
                "bounded GenLayer transaction returned an invalid result"
            )
        return result

    def _validate_write_result(self, method: str, result: dict) -> None:
        output = json.dumps(result)
        execution_result = result.get("txExecutionResultName")
        transaction_hash = result.get("transactionHash")
        transaction_suffix = (
            f" (transaction {transaction_hash})"
            if isinstance(transaction_hash, str)
            else ""
        )
        if result.get("statusName") == "LEADER_TIMEOUT":
            raise GenLayerGatewayError(
                f"GenLayer write failed for {method}: LEADER_TIMEOUT"
                f"{transaction_suffix}"
            )
        if execution_result == "FINISHED_WITH_ERROR":
            raise IntegrityCheckError(self._write_rejection_message(method, output))
        if execution_result != "FINISHED_WITH_RETURN":
            raise GenLayerGatewayError(
                f"GenLayer write for {method} did not return a successful "
                f"execution receipt{transaction_suffix}"
            )
        if result.get("resultName") != "AGREE":
            consensus_result = result.get("resultName") or "UNKNOWN"
            raise GenLayerGatewayError(
                f"GenLayer write for {method} did not reach validator "
                f"consensus ({consensus_result}){transaction_suffix}"
            )

    def _run_read_process(self, cmd) -> str:
        last_error = None
        for attempt in range(READ_RETRY_ATTEMPTS):
            try:
                return self._run_process(cmd)
            except GenLayerGatewayError as exc:
                last_error = exc
                normalized = str(exc).lower()
                retryable = any(
                    marker in normalized
                    for marker in RETRYABLE_TRANSPORT_MARKERS
                )
                if not retryable or attempt == READ_RETRY_ATTEMPTS - 1:
                    raise
                time.sleep(READ_RETRY_BASE_DELAY_SECONDS * (2 ** attempt))
        raise last_error

    def _run(self, action: str, method: str, args=None):
        args = args or []
        self._ensure_cli_ready()

        if action == "write":
            try:
                result = self._run_bounded_transaction(
                    "write",
                    method=method,
                    args=args,
                )
            except GenLayerGatewayError as exc:
                raise GenLayerGatewayError(
                    f"GenLayer write failed for {method}: {exc}"
                ) from exc
            self._validate_write_result(method, result)
            return json.dumps(result)

        cmd = [*self.cli_command, action, self.contract_address, method]
        if self.rpc_url:
            cmd.extend(["--rpc", self.rpc_url])
        if args:
            cmd.append("--args")
            cmd.extend(self._arg(item) for item in args)

        try:
            output = (
                self._run_read_process(cmd)
                if action == "call"
                else self._run_process(cmd)
            )
        except GenLayerGatewayError as exc:
            raise GenLayerGatewayError(f"GenLayer {action} failed for {method}: {exc}") from exc
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

    def deploy(self, contract_path: str, args=None) -> dict:
        with self._write_lock:
            self._ensure_cli_ready()
            result = self._run_bounded_transaction(
                "deploy",
                contract_path=contract_path,
                args=args,
            )
            self._validate_write_result("deploy", result)
            if not result.get("contractAddress"):
                raise GenLayerGatewayError(
                    "GenLayer deployment returned no contract address"
                )
            return result

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
            protocol_snapshot = self._fetch_protocol_snapshot(
                clinicaltrials_gov_url
            )
            self.write("register_trial", [
                trial_id,
                clinicaltrials_gov_url,
                primary_hypothesis,
                primary_endpoints,
                expected_sample_size,
                sponsor_wallet,
                integrity_bond,
                protocol_snapshot,
            ])
            return self.get_trial(trial_id)

    def submit_results(self, trial_id, report_id, publication_url, preprint_url=""):
        with self._write_lock:
            trial = self.get_trial(trial_id)
            registry_url = str(trial.get("registry_url", "")).strip()
            if not registry_url:
                raise GenLayerGatewayError(
                    "trial contains no ClinicalTrials.gov registry URL"
                )
            current_registry_snapshot = self._fetch_protocol_snapshot(
                registry_url
            )
            publication_snapshot = self._fetch_document_snapshot(
                publication_url
            )
            preprint_snapshot = (
                self._fetch_document_snapshot(preprint_url)
                if preprint_url
                else ""
            )
            self.write("submit_results", [
                trial_id,
                report_id,
                publication_url,
                preprint_url or "",
                current_registry_snapshot,
                publication_snapshot,
                preprint_snapshot,
            ])
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
