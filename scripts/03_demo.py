"""End-to-end demo, mapped 1:1 to the five things SIH26117 asks you to show."""
from __future__ import annotations

import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rich.console import Console
from rich.panel import Panel

from app.agent.loop import Agent
from app.core.audit import AuditLog
from app.core.config import get_settings
from app.graphrag.retrieve import GraphRetriever
from app.graphrag.store import GraphStore
from app.retrieval.vector import VectorIndex
from app.router.manager import get_manager
from app.sentinel.monitor import Sentinel
from app.tools import registry

c = Console()
s = get_settings()


def main():
    store = GraphStore(s.stores_dir / "graph")
    index = VectorIndex(s.stores_dir / "qdrant")
    mgr = get_manager()
    audit = AuditLog(s.stores_dir / "audit.jsonl")
    retriever = GraphRetriever(store, index, mgr)
    registry.register_all({"store": store, "index": index, "retriever": retriever})
    agent = Agent(mgr, audit, max_iterations=1, max_steps=5)

    goal = ("Using graph_query on P-101A and the calculate tool, determine which "
            "equipment is affected if P-101A is isolated, then produce an approval "
            "note as a docx recommending seal replacement.")
    c.print(Panel(goal, title="GOAL"))
    t0 = time.time()
    r = agent.run(goal)
    c.print(Panel(r.answer[:2000], title=f"ANSWER ({time.time()-t0:.0f}s)"))

    c.print("\n[bold]Router decisions (rubric point 1)[/bold]")
    for d in r.decisions:
        c.print(f"  task={d['task']:10} -> {d['tag']:14} {d['reason']}")

    c.print("\n[bold]Steps executed[/bold]")
    for st in r.steps:
        c.print(f"  [{'ok' if st.ok else 'FAIL'}] {st.tool:20} {st.ms:>6} ms  {st.intent[:60]}")

    c.print(f"\n[bold]Artifacts (rubric point 2)[/bold] {r.artifacts}")

    sen = Sentinel()
    c.print(f"\n[bold]Sovereignty (rubric point 5)[/bold]")
    c.print(f"  canary: {sen.canary()}")
    ok, bad = audit.verify()
    c.print(f"  audit chain valid: {ok} (first bad index: {bad})")


if __name__ == "__main__":
    main()
