"""Sandboxed code execution.

Docker is unavailable on this machine (needs root), so the default backend is
an unprivileged user+network namespace via `unshare -rn`, which gives the one
property that actually matters here: the child gets a fresh network namespace
with only a down loopback, so generated code physically cannot reach the
network. Layered on top: RLIMIT caps, a scratch cwd, a scrubbed environment
and a wall-clock timeout.

If Docker is later installed, set backend="docker" for stronger filesystem and
kernel isolation. Capability is detected at runtime and reported by probe().
"""
from __future__ import annotations

import os
import resource
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

DENY_ENV = {"AWS_", "OPENAI_", "ANTHROPIC_", "HF_TOKEN", "SSH_", "GITHUB_"}


@dataclass
class ExecResult:
    ok: bool
    stdout: str
    stderr: str
    exit_code: int
    backend: str
    files: list[str]


def _limits(cpu_s: int, mem_mb: int, nproc: int = 64):
    def _apply():
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s))
        resource.setrlimit(resource.RLIMIT_AS, (mem_mb * 1024**2,) * 2)
        resource.setrlimit(resource.RLIMIT_NPROC, (nproc, nproc))
        resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024**2,) * 2)
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        os.setsid()
    return _apply


def _clean_env() -> dict:
    env = {k: v for k, v in os.environ.items()
           if not any(k.startswith(p) for p in DENY_ENV)}
    env.update({"HOME": "/tmp", "PYTHONDONTWRITEBYTECODE": "1",
                "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                "no_proxy": "*", "NO_PROXY": "*"})
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        env.pop(k, None)
    return env


def probe() -> dict:
    """Report which isolation backends this machine supports."""
    caps = {"docker": shutil.which("docker") is not None, "netns": False}
    if shutil.which("unshare"):
        try:
            r = subprocess.run(["unshare", "-rn", "true"], capture_output=True, timeout=10)
            caps["netns"] = r.returncode == 0
        except Exception:
            pass
    return caps


def run_python(code: str, *, timeout: int = 30, mem_mb: int = 2048,
               workdir: Path | None = None, python: str | None = None) -> ExecResult:
    caps = probe()
    backend = "docker" if caps["docker"] else ("unshare-netns" if caps["netns"] else "rlimit-only")
    wd = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="sbx-"))
    wd.mkdir(parents=True, exist_ok=True)
    src = wd / "main.py"
    src.write_text(code)
    py = python or os.environ.get("SANDBOX_PYTHON", "python3")

    if backend == "docker":
        cmd = ["docker", "run", "--rm", "--network", "none", "--memory", f"{mem_mb}m",
               "--cpus", "1", "--pids-limit", "128", "--read-only",
               "--tmpfs", "/tmp:rw,size=64m", "-v", f"{wd}:/w:rw", "-w", "/w",
               "python:3.11-slim", "python", "main.py"]
        pre = None
    elif backend == "unshare-netns":
        cmd = ["unshare", "-rn", "--", py, "main.py"]
        pre = _limits(timeout, mem_mb)
    else:
        cmd = [py, "main.py"]
        pre = _limits(timeout, mem_mb)

    before = {p.name for p in wd.iterdir()}
    try:
        p = subprocess.run(cmd, cwd=wd, capture_output=True, text=True,
                           timeout=timeout, env=_clean_env(), preexec_fn=pre)
        out, err, rc = p.stdout, p.stderr, p.returncode
    except subprocess.TimeoutExpired:
        out, err, rc = "", f"timeout after {timeout}s", 124
    except Exception as e:
        out, err, rc = "", f"sandbox error: {e}", 1

    new = [p.name for p in wd.iterdir() if p.name not in before]
    return ExecResult(rc == 0, out[-8000:], err[-4000:], rc, backend, sorted(new))
