"""Append-only, hash-chained audit log.

Every prompt, model selection, tool call and file write lands here. Each record
embeds the SHA-256 of the previous record, so any tampering or deletion breaks
the chain and `verify()` reports the first bad index. This is the compliance
half of the sovereignty story; app/sentinel/monitor.py is the network half.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
GENESIS = "0" * 64


class AuditLog:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def _last_hash(self) -> str:
        last = GENESIS
        with open(self.path) as fh:
            for line in fh:
                if line.strip():
                    last = json.loads(line)["hash"]
        return last

    def append(self, event: str, **payload: Any) -> dict:
        with _LOCK:
            rec = {
                "ts": time.time(),
                "event": event,
                "payload": payload,
                "prev": self._last_hash(),
            }
            body = json.dumps(rec, sort_keys=True, default=str)
            rec["hash"] = hashlib.sha256(body.encode()).hexdigest()
            with open(self.path, "a") as fh:
                fh.write(json.dumps(rec, default=str) + "\n")
            return rec

    def verify(self) -> tuple[bool, int | None]:
        """Return (ok, first_bad_index)."""
        prev = GENESIS
        with open(self.path) as fh:
            for i, line in enumerate(fh):
                if not line.strip():
                    continue
                rec = json.loads(line)
                if rec["prev"] != prev:
                    return False, i
                stored = rec.pop("hash")
                body = json.dumps(rec, sort_keys=True, default=str)
                if hashlib.sha256(body.encode()).hexdigest() != stored:
                    return False, i
                prev = stored
        return True, None

    def tail(self, n: int = 50) -> list[dict]:
        with open(self.path) as fh:
            lines = [l for l in fh if l.strip()]
        return [json.loads(l) for l in lines[-n:]]
