"""Egress monitor - the visible half of the sovereignty proof.

Samples outbound connections owned by this process tree and by the Ollama
server, and classifies each as local or external. A judge should be able to
watch `external_total` stay at zero for the whole demo, then press the canary
button and watch `blocked_attempts` increment.
"""
from __future__ import annotations

import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field

LOCAL_PREFIXES = ("127.", "::1", "0.0.0.0", "[::]", "*", "localhost")


def _is_local(addr: str) -> bool:
    host = addr.rsplit(":", 1)[0].strip("[]")
    return host.startswith(LOCAL_PREFIXES) or host in ("", "*")


def snapshot() -> dict:
    """Current established TCP/UDP connections, split local vs external."""
    local, external = [], []
    try:
        out = subprocess.run(["ss", "-tunp"], capture_output=True, text=True,
                             timeout=5).stdout
    except Exception:
        return {"available": False, "local": [], "external": [], "error": "ss unavailable"}
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 6:
            continue
        peer = parts[5]
        (local if _is_local(peer) else external).append(peer)
    return {"available": True, "local": local, "external": external}


@dataclass
class Sentinel:
    interval: float = 2.0
    external_total: int = 0
    blocked_attempts: int = 0
    started: float = field(default_factory=time.time)
    last: dict = field(default_factory=dict)
    _stop: threading.Event = field(default_factory=threading.Event)

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            s = snapshot()
            self.last = s
            self.external_total += len(s.get("external", []))
            self._stop.wait(self.interval)

    def stop(self) -> None:
        self._stop.set()

    def canary(self, host: str = "1.1.1.1", port: int = 53, timeout: float = 3.0) -> dict:
        """Deliberately attempt an external connection and report the result.
        Reaching the internet here means the deployment is NOT sovereign."""
        t0 = time.time()
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return {"blocked": False, "verdict": "REACHED EXTERNAL HOST",
                        "detail": f"connected to {host}:{port}",
                        "ms": int((time.time() - t0) * 1000)}
        except Exception as e:
            self.blocked_attempts += 1
            return {"blocked": True, "verdict": "BLOCKED",
                    "detail": f"{type(e).__name__}: {e}",
                    "ms": int((time.time() - t0) * 1000)}

    def status(self) -> dict:
        return {"uptime_s": int(time.time() - self.started),
                "external_connections": self.external_total,
                "blocked_attempts": self.blocked_attempts,
                "local_connections": len(self.last.get("local", [])),
                "sovereign": self.external_total == 0}
