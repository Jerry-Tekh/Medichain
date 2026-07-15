"""JSON-backed persistence for local MediChain mode."""

import json
from pathlib import Path
import threading

from medichain_contract import MediChainContract


class PersistentMediChainContract(MediChainContract):
    def __init__(self, webpage_fetcher, llm_client, state_path: str):
        self._state_path = Path(state_path)
        self._state_lock = threading.RLock()
        super().__init__(webpage_fetcher, llm_client)
        self._load_state()

    def _load_state(self) -> None:
        if not self._state_path.exists():
            return
        data = json.loads(self._state_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("MediChain state file must contain a JSON object")
        self.trials = data.get("trials", {})
        self.integrity_reports = data.get("integrity_reports", {})
        self.flags = data.get("flags", {})

    def _save_state(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "trials": self.trials,
            "integrity_reports": self.integrity_reports,
            "flags": self.flags,
        }
        tmp_path = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.chmod(0o600)
        tmp_path.replace(self._state_path)

    def register_trial(self, *args, **kwargs):
        with self._state_lock:
            result = super().register_trial(*args, **kwargs)
            self._save_state()
            return result

    def submit_results(self, *args, **kwargs):
        with self._state_lock:
            result = super().submit_results(*args, **kwargs)
            self._save_state()
            return result

    def resolve_appeal(self, *args, **kwargs):
        with self._state_lock:
            result = super().resolve_appeal(*args, **kwargs)
            self._save_state()
            return result

    def submit_flag(self, *args, **kwargs):
        with self._state_lock:
            result = super().submit_flag(*args, **kwargs)
            self._save_state()
            return result
