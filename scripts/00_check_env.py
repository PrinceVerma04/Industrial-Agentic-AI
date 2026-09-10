"""Preflight. Run this first, and again at the venue before the demo."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rich.console import Console
from rich.table import Table

from app.core.config import get_manifest, get_settings
from app.core.llm import get_client
from app.tools.sandbox import probe

c = Console()


def main() -> int:
    t = Table(title="Sovereign Workbench - preflight", show_lines=False)
    t.add_column("check"); t.add_column("status"); t.add_column("detail")
    fail = 0

    t.add_row("python", "OK", sys.version.split()[0])

    try:
        import torch
        cap = torch.cuda.get_device_capability(0) if torch.cuda.is_available() else None
        free = torch.cuda.mem_get_info()[0] / 1e9 if torch.cuda.is_available() else 0
        t.add_row("gpu", "OK" if cap else "WARN",
                  f"{torch.cuda.get_device_name(0)} sm_{cap[0]}{cap[1]} · {free:.1f} GB free"
                  if cap else "no CUDA")
    except Exception as e:
        t.add_row("gpu", "WARN", str(e)[:60])

    llm = get_client()
    try:
        have = {m["name"] for m in llm.list_models()}
        t.add_row("ollama", "OK", f"{len(have)} models @ {llm.host}")
    except Exception as e:
        t.add_row("ollama", "FAIL", str(e)[:60]); have = set(); fail += 1

    mf = get_manifest()
    prof = get_settings().profile
    needed = {m["id"]: m["tag"] for m in mf["models"]
              if m["id"] in mf["profiles"][prof]["models"]}
    for mid, tag in needed.items():
        ok = tag in have or f"{tag}:latest" in have
        t.add_row(f"model:{mid}", "OK" if ok else "MISSING", tag)
        fail += 0 if ok else 1

    caps = probe()
    backend = "docker" if caps["docker"] else ("unshare-netns" if caps["netns"] else "rlimit-only")
    t.add_row("sandbox", "OK" if backend != "rlimit-only" else "WARN", backend)

    for tool in ("ss", "nft", "bpftrace", "tcpdump"):
        t.add_row(f"bin:{tool}", "OK" if shutil.which(tool) else "MISSING", tool)

    s = get_settings()
    t.add_row("data dirs", "OK", str(s.workbench_data_dir))

    c.print(t)
    c.print(f"[bold]{'READY' if not fail else f'{fail} BLOCKING ISSUE(S)'}[/bold]  profile={prof}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
