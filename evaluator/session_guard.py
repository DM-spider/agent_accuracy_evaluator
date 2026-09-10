"""Persistent session lease: a process restart must not replay an uncertain POST."""
import hashlib
import json
from pathlib import Path


class SessionGuard:
    def __init__(self, root: Path, session_id: str):
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / (hashlib.sha256(session_id.encode()).hexdigest() + ".json")

    def acquire(self, run_id: str):
        with self.path.open("x", encoding="utf-8") as f:
            json.dump({"run_id": run_id, "state": "running_or_unconfirmed"}, f)

    def release(self):
        self.path.unlink(missing_ok=True)

    def blocked(self):
        return self.path.exists()
