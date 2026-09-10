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


@app.get("/", response_class=HTMLResponse)
def ui():
    return (Path(__file__).resolve().parents[1] / "ui" / "index.html").read_text()


@app.get("/health")
def health():
    return {"ok": True, "profile": manager.profile,
            "graph": store.stats(), "tools": len(registry.all_tools())}


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
    agent = Agent(manager, audit, max_iterations=req.max_iterations)
    r = agent.run(req.goal)
    return {"answer": r.answer, "iterations": r.iterations,
            "artifacts": r.artifacts, "decisions": r.decisions,
            "steps": [{"tool": s.tool, "intent": s.intent, "ok": s.ok, "ms": s.ms}
                      for s in r.steps]}


@app.get("/search")
def search(q: str, k: int = 8):
    res = retriever.retrieve(q, k)
    return {"mode": res["plan"].mode, "why": res["plan"].reason,
            "nodes": res.get("nodes", []),
            "chunks": [{"doc_id": c.get("doc_id"), "page": c.get("page"),
                        "text": c["text"][:400]} for c in res.get("chunks", [])]}


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
