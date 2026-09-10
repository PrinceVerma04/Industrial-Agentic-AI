"""FastAPI service. Binds to loopback only - there is no interface on which
this can be reached from another machine."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from app.agent.loop import Agent
from app.core.audit import AuditLog
from app.core.config import get_settings
from app.graphrag.retrieve import GraphRetriever
from app.graphrag.store import GraphStore
from app.retrieval.vector import VectorIndex
from app.router.manager import get_manager
from app.sentinel.monitor import Sentinel
from app.tools import registry

settings = get_settings()
app = FastAPI(title="Sovereign On-Premise Agentic AI Workbench")

store = GraphStore(settings.stores_dir / "graph")
index = VectorIndex(settings.stores_dir / "qdrant")
manager = get_manager()
audit = AuditLog(settings.stores_dir / "audit.jsonl")
retriever = GraphRetriever(store, index, manager)
sentinel = Sentinel()
registry.register_all({"store": store, "index": index, "retriever": retriever})


@app.on_event("startup")
def _startup():
    sentinel.start()
    audit.append("startup", profile=manager.profile, budget_gb=manager.budget)


class AskRequest(BaseModel):
    goal: str
    max_iterations: int = 2
    max_steps: int = 8


@app.get("/", response_class=HTMLResponse)
def ui():
    return (Path(__file__).resolve().parents[1] / "ui" / "index.html").read_text()


def _edge_total() -> int:
    from app.graphrag.schema import rel_types
    total, seen = 0, set()
    for rel, a, b in rel_types():
        if (rel, a, b) in seen:
            continue
        seen.add((rel, a, b))
        try:
            r = store.rows(f"MATCH (x:{a})-[e:{rel}]->(y:{b}) RETURN count(e) AS c")
            total += r[0]["c"] if r else 0
        except Exception:
            pass
    return total


@app.get("/health")
def health():
    node_counts = store.stats()
    try:
        vector_count = index.client.count("chunks").count
    except Exception:
        vector_count = 0
    return {"ok": True, "profile": manager.profile,
            "graph": node_counts, "graph_nodes": sum(node_counts.values()),
            "graph_edges": _edge_total(), "vector_chunks": vector_count,
            "tools": len(registry.all_tools())}


@app.get("/sovereignty")
def sovereignty():
    from app.tools.sandbox import probe
    ok, bad = audit.verify()
    return {**sentinel.status(), "sandbox": probe(),
            "audit_chain_valid": ok, "audit_first_bad_index": bad}


@app.post("/sovereignty/canary")
def canary():
    """Deliberate external-call attempt. Must be blocked on a sovereign box."""
    r = sentinel.canary()
    audit.append("canary", **r)
    return r


@app.get("/models")
def models():
    return {"profile": manager.profile, "budget_gb": manager.budget,
            "resident": list(manager.resident), "used_gb": manager.used_gb(),
            "catalogue": [s.__dict__ for s in manager.specs.values()],
            "decisions": [d.__dict__ for d in manager.decisions[-30:]]}


@app.post("/ask")
def ask(req: AskRequest):
    agent = Agent(manager, audit, max_iterations=req.max_iterations,
                  max_steps=req.max_steps)
    r = agent.run(req.goal)
    return {"answer": r.answer, "iterations": r.iterations,
            "artifacts": r.artifacts, "decisions": r.decisions,
            "steps": [{"tool": s.tool, "intent": s.intent, "ok": s.ok, "ms": s.ms,
                       "arguments": s.arguments, "output": str(s.output)[:4000]}
                      for s in r.steps]}


@app.get("/search")
def search(q: str, k: int = 8):
    res = retriever.retrieve(q, k)
    return {"mode": res["plan"].mode, "why": res["plan"].reason,
            "anchors": res["plan"].anchors,
            "nodes": res.get("nodes", []),
            "communities": res.get("communities", []),
            "chunks": [{"doc_id": c.get("doc_id"), "page": c.get("page"),
                        "text": c["text"][:2500]} for c in res.get("chunks", [])]}


@app.get("/graph/neighborhood")
def graph_neighborhood(entity: str, hops: int = 2):
    hits = store.find(entity.strip())
    if not hits:
        return {"found": False}
    anchor = hits[0]
    rows = store.neighborhood(anchor["type"], anchor["key"], hops=hops)
    rows = [r for r in rows if r["key"] != anchor["key"]]
    one_hop = {r["key"] for r in store.neighborhood(anchor["type"], anchor["key"], hops=1)
               if r["key"] != anchor["key"]}
    indirect = sorted(r["name"] for r in rows if r["key"] not in one_hop)
    return {"found": True, "anchor": anchor, "rows": rows, "indirect_only": indirect}


@app.get("/audit")
def audit_tail(n: int = 50):
    ok, bad = audit.verify()
    return {"chain_valid": ok, "first_bad_index": bad, "records": audit.tail(n)}


@app.get("/artifacts/{name}")
def artifact(name: str):
    p = (settings.artifacts_dir / name).resolve()
    if settings.artifacts_dir.resolve() not in p.parents or not p.is_file():
        return {"error": "not found"}
    return FileResponse(p)
