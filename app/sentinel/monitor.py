"""Egress monitor - the visible half of the sovereignty proof.

Samples outbound connections owned by this process tree and by the Ollama
server, and classifies each as local or external. A judge should be able to
watch `external_total` stay at zero for the whole demo, then press the canary
button and watch `blocked_attempts` increment.
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field

LOCAL_PREFIXES = ("127.", "::1", "0.0.0.0", "[::]", "*", "localhost")
_PID_RE = re.compile(r"pid=(\d+)")


def _is_local(addr: str) -> bool:
    host = addr.rsplit(":", 1)[0].strip("[]")
    return host.startswith(LOCAL_PREFIXES) or host in ("", "*")


def _own_process_tree(root_pid: int) -> set[int]:
    """root_pid and every descendant, read from /proc - this API process
    plus whatever tools/subprocesses it has spawned."""
    children: dict[int, list[int]] = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/status") as f:
                ppid = None
                for line in f:
                    if line.startswith("PPid:"):
                        ppid = int(line.split()[1])
                        break
            if ppid is not None:
                children.setdefault(ppid, []).append(int(entry))
        except (OSError, ValueError):
            continue

    tree, stack = {root_pid}, [root_pid]
    while stack:
        for kid in children.get(stack.pop(), []):
            if kid not in tree:
                tree.add(kid)
                stack.append(kid)
    return tree


def _ollama_pids() -> set[int]:
    pids = set()
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/comm") as f:
                if f.read().strip() == "ollama":
                    pids.add(int(entry))
        except OSError:
            continue
    return pids


def _relevant_pids() -> set[int]:
    """This process's tree (API + anything it spawns) plus the Ollama
    server. Anything else on the machine - a browser, VS Code, apt - is not
    this system's egress and must not count toward the sovereignty verdict."""
    return _own_process_tree(os.getpid()) | _ollama_pids()


def snapshot() -> dict:
    """Established TCP/UDP connections owned by this process tree or Ollama,
    split local vs external. Connections from unrelated processes on the
    machine are excluded entirely - counting them would make the sovereignty
    verdict depend on what else happens to be running on the laptop."""
    local, external = [], []
    try:
        out = subprocess.run(["ss", "-tunp"], capture_output=True, text=True,
                             timeout=5).stdout
    except Exception:
        return {"available": False, "local": [], "external": [], "error": "ss unavailable"}
    relevant = _relevant_pids()
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 6:
            continue
        m = _PID_RE.search(parts[-1])
        if not m or int(m.group(1)) not in relevant:
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
