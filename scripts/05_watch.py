"""Live indexing monitor.

The build's stdout is block-buffered, so tailing its log tells you nothing
until it exits. The extraction cache is SQLite and safe to read while the
build writes, so progress is derived from there instead. The Kuzu graph is
NOT touched - it is exclusively locked by the writer.

    python scripts/05_watch.py            # live dashboard
    python scripts/05_watch.py --once     # single reading, for scripts
"""
from __future__ import annotations

import re
import sqlite3
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn, TimeRemainingColumn
from rich.table import Table

from app.core.config import get_settings
from app.ingest.pipeline import ingest_dir

console = Console()
S = get_settings()
CACHE = S.stores_dir / "extract_cache.db"


def cached() -> int:
    if not CACHE.exists():
        return 0
    try:
        db = sqlite3.connect(f"file:{CACHE}?mode=ro", uri=True, timeout=2)
        n = db.execute("SELECT count(*) FROM cache").fetchone()[0]
        db.close()
        return n
    except sqlite3.Error:
        return -1


def build_pid() -> int | None:
    r = subprocess.run(["pgrep", "-f", "02_build_index"], capture_output=True, text=True)
    pids = [int(p) for p in r.stdout.split()]
    return pids[0] if pids else None


def elapsed(pid: int) -> int:
    out = subprocess.run(["ps", "-o", "etime=", "-p", str(pid)],
                         capture_output=True, text=True).stdout.strip()
    parts = [int(x) for x in re.split(r"[-:]", out)] if out else [0]
    mult, sec = [1, 60, 3600, 86400], 0
    for i, v in enumerate(reversed(parts)):
        sec += v * mult[i]
    return sec


def gpu() -> str:
    q = ("utilization.gpu,memory.used,memory.total,temperature.gpu")
    r = subprocess.run(["nvidia-smi", f"--query-gpu={q}", "--format=csv,noheader,nounits"],
                       capture_output=True, text=True).stdout.strip()
    if not r:
        return "n/a"
    u, mu, mt, t = [x.strip() for x in r.split(",")]
    return f"{u}% · {int(mu)/1024:.1f}/{int(mt)/1024:.1f} GB · {t}°C"


def loaded_model() -> str:
    try:
        import httpx
        m = httpx.get("http://127.0.0.1:11434/api/ps", timeout=3).json()["models"]
        return ", ".join(f"{x['name']} ({x['size_vram']/1e9:.1f} GB)" for x in m) or "none"
    except Exception:
        return "unreachable"


def render(total: int, done: int, pid: int | None, secs: int, hist: deque) -> Group:
    t = Table.grid(padding=(0, 2))
    t.add_column(style="dim", width=14); t.add_column()

    state = f"[green]running[/] (pid {pid})" if pid else "[yellow]not running[/]"
    t.add_row("build", state)
    t.add_row("chunks", f"[bold]{done}[/] / {total}" + (" [dim](cache)[/]" if done else ""))
    t.add_row("elapsed", f"{secs//3600}h {secs%3600//60:02d}m {secs%60:02d}s" if secs else "-")

    if done and secs:
        avg = secs / done
        t.add_row("avg rate", f"{avg:.0f}s / chunk")
        if len(hist) >= 2:
            (d0, s0), (d1, s1) = hist[0], hist[-1]
            if d1 > d0:
                recent = (s1 - s0) / (d1 - d0)
                trend = "[red]slowing[/]" if recent > avg * 1.3 else (
                    "[green]speeding up[/]" if recent < avg * 0.7 else "steady")
                t.add_row("recent rate", f"{recent:.0f}s / chunk  {trend}")
                left = (total - done) * recent
                t.add_row("ETA", f"~{left/60:.0f} min ({left/3600:.1f} h)")
    t.add_row("gpu", gpu())
    t.add_row("model", loaded_model())

    p = Progress(TextColumn("[progress.description]{task.description}"),
                 BarColumn(bar_width=46),
                 TextColumn("{task.completed}/{task.total}"),
                 TimeRemainingColumn(), expand=False)
    p.add_task("extract", total=total, completed=max(done, 0))

    note = ("\n[dim]Ctrl-C stops this watcher only — the build keeps running.\n"
            "Extraction is cached, so a restart resumes where it left off.[/]")
    return Group(Panel(t, title="Sovereign Workbench · indexing", border_style="cyan"),
                 p, note)


def main() -> None:
    total = len(ingest_dir(S.corpus_dir)) or 1
    hist: deque = deque(maxlen=12)

    if "--once" in sys.argv:
        pid = build_pid()
        console.print(render(total, cached(), pid, elapsed(pid) if pid else 0, hist))
        return

    with Live(console=console, refresh_per_second=2, screen=False) as live:
        while True:
            pid = build_pid()
            done, secs = cached(), elapsed(pid) if pid else 0
            if pid and done >= 0:
                hist.append((done, secs))
            live.update(render(total, done, pid, secs, hist))
            if not pid and done >= total:
                live.update(Group(render(total, done, None, secs, hist),
                                  Panel("[bold green]BUILD COMPLETE[/]")))
                break
            time.sleep(5)


if __name__ == "__main__":
    main()
