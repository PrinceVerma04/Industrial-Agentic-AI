"""Streamlit test console for the Sovereign On-Premise Agentic AI Workbench.

Purpose is inspection, not presentation: every tab shows what the system
actually did --- which retrieval mode fired and why, which model was selected
and what it evicted, each tool call with its real arguments and raw output,
and the generated file rendered so you can see its contents rather than trust
a success message. Several of this project's worst defects were invisible in
status lines and obvious the moment the artefact was opened.

    streamlit run streamlit_app.py

NOTE: this process holds the K\u00f9zu write lock, so `make index` cannot run
while it is up. Stop it before re-indexing.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
import streamlit as st

from app.agent.loop import Agent
from app.core.audit import AuditLog
from app.core.config import get_manifest, get_settings
from app.graphrag.communities import CommunityStore
from app.graphrag.retrieve import GraphRetriever, format_context
from app.graphrag.schema import NODE_TYPES, REL_PAIRS, rel_types
from app.graphrag.store import GraphStore
from app.retrieval.vector import VectorIndex
from app.router.manager import get_manager
from app.sentinel.monitor import Sentinel
from app.tools import registry
from app.tools.sandbox import probe, run_python

st.set_page_config(page_title="Sovereign AI Workbench",
                   page_icon="\U0001F512", layout="wide")

S = get_settings()


# ------------------------------------------------------------------ wiring --
@st.cache_resource(show_spinner="Opening stores and model manager...")
def boot():
    # Read-only: this console never writes to the graph, so it must not take
    # the exclusive write lock and block `make index`.
    graph_path = S.stores_dir / "graph"
    if not graph_path.exists():
        store = GraphStore(graph_path)   # first run: create schema (writer)
    else:
        try:
            store = GraphStore(graph_path, read_only=True)
        except Exception as e:
            # Store already exists - never fall back to a write open here,
            # that would grab the exclusive lock and collide with the API or
            # `make index`. Surface the real cause instead (see docs/concurrency).
            raise RuntimeError(
                "Could not open the graph store read-only. Another process may "
                "still be creating it, or a stale lock is held. Check with: "
                "fuser -v data/stores/graph"
            ) from e
    index = VectorIndex(S.stores_dir / "qdrant")
    manager = get_manager()
    audit = AuditLog(S.stores_dir / "audit.jsonl")
    retriever = GraphRetriever(store, index, manager)
    registry.register_all({"store": store, "index": index, "retriever": retriever})
    sentinel = Sentinel()
    sentinel.start()
    return store, index, manager, audit, retriever, sentinel


try:
    store, index, manager, audit, retriever, sentinel = boot()
except RuntimeError as e:
    st.error(f"Could not open the graph store: {e}")
    st.info("Another process holds the write lock. Stop `make index` or a second "
            "copy of this app, then reload. Check with:  "
            "`fuser -v data/stores/graph`")
    st.stop()


def vector_count() -> int:
    try:
        return index.client.count("chunks").count
    except Exception:
        return 0


def graph_totals() -> tuple[int, int]:
    nodes = sum(store.stats().values())
    edges, seen = 0, set()
    for rel, a, b in rel_types():
        if (rel, a, b) in seen:
            continue
        seen.add((rel, a, b))
        try:
            r = store.rows(f"MATCH (x:{a})-[e:{rel}]->(y:{b}) RETURN count(e) AS c")
            edges += r[0]["c"] if r else 0
        except Exception:
            pass
    return nodes, edges


# ------------------------------------------------------------------ sidebar -
with st.sidebar:
    st.title("\U0001F512 Workbench")
    st.caption("SIH26117 \u00b7 MRPL \u00b7 on-premise, air-gapped")

    st.subheader("Sovereignty")
    stat = sentinel.status()
    caps = probe()
    backend = "docker" if caps["docker"] else ("netns" if caps["netns"] else "rlimit only")
    ok, bad = audit.verify()

    c1, c2 = st.columns(2)
    c1.metric("External calls", stat["external_connections"])
    c2.metric("Blocked", stat["blocked_attempts"])
    st.write(f"Sandbox: **{backend}**")
    st.write("Audit chain: " + ("**valid**" if ok else f"**BROKEN at {bad}**"))

    if st.button("Attempt external call", use_container_width=True):
        r = sentinel.canary()
        audit.append("canary", **r)
        (st.success if r["blocked"] else st.warning)(
            f"{r['verdict']} \u2014 {r['detail']}")
        if not r["blocked"]:
            st.caption("Expected on a machine with internet. Run "
                       "`sudo ./scripts/04_lockdown.sh on` to enforce denial.")

    st.divider()
    st.subheader("Index")
    n_nodes, n_edges = graph_totals()
    st.write(f"Graph: **{n_nodes}** nodes / **{n_edges}** edges")
    st.write(f"Vectors: **{vector_count()}** chunks")
    try:
        st.write(f"Communities: **{len(CommunityStore(S.stores_dir/'communities.db').all())}**")
    except Exception:
        st.write("Communities: 0")
    st.caption(f"profile `{manager.profile}` \u00b7 ontology `{S.ontology}`")
    st.caption(f"budget {manager.budget} GB \u00b7 resident {manager.used_gb():.1f} GB")


tabs = st.tabs(["Ask", "Search", "Graph", "Documents", "Models", "Audit"])

# ---------------------------------------------------------------------- ASK -
with tabs[0]:
    st.subheader("Run an agentic goal")
    st.caption("Plan \u2192 execute tools \u2192 critique \u2192 synthesise. "
               "Typically 45\u2013120 s. Always open the generated file.")

    examples = [
        "What skills appear across the resumes?",
        "Summarise the main contribution of the electronics paper with citations.",
        "Which candidate has the most machine learning experience? Explain why.",
        "Compare the candidates and write a short docx summary.",
    ]
    pick = st.selectbox("Example goals", ["(write my own)"] + examples)
    goal = st.text_area("Goal", value="" if pick.startswith("(") else pick, height=90)

    c1, c2, _ = st.columns([1, 1, 3])
    iters = c1.number_input("Max iterations", 1, 3, 1)
    steps = c2.number_input("Max steps", 1, 8, 4)

    if st.button("Run agent", type="primary", disabled=not goal.strip()):
        agent = Agent(manager, audit, max_iterations=int(iters), max_steps=int(steps))
        t0 = time.time()
        with st.spinner("Planning and executing..."):
            try:
                res = agent.run(goal)
            except Exception as e:
                st.error(f"{type(e).__name__}: {e}")
                st.stop()
        st.success(f"Completed in {time.time()-t0:.0f}s \u00b7 {res.iterations} iteration(s)")

        st.markdown("### Answer")
        st.markdown(res.answer or "_no answer produced_")

        st.markdown("### Steps")
        for s in res.steps:
            icon = "\u2705" if s.ok else "\u274C"
            with st.expander(f"{icon} `{s.tool}` \u00b7 {s.ms} ms \u00b7 {s.intent}",
                             expanded=not s.ok):
                st.caption("Arguments actually passed (after coercion)")
                st.json(s.arguments, expanded=False)
                st.caption("Raw tool output")
                st.code(str(s.output)[:4000])

        if res.decisions:
            st.markdown("### Model routing")
            st.dataframe(pd.DataFrame(res.decisions)[
                ["task", "chosen", "tag", "reason", "evicted", "load_ms"]],
                use_container_width=True, hide_index=True)

        st.markdown("### Artefacts")
        if not res.artifacts:
            st.info("No file was generated.")
        for a in res.artifacts:
            p = Path(a)
            if not p.exists():
                st.error(f"{a} \u2014 reported but missing")
                continue
            st.write(f"**{p.name}** \u00b7 {p.stat().st_size:,} bytes")
            st.download_button("Download", p.read_bytes(), p.name,
                               key=f"dl-{p.name}")
            try:
                if p.suffix == ".xlsx":
                    st.dataframe(pd.read_excel(p), use_container_width=True)
                elif p.suffix == ".docx":
                    from docx import Document
                    st.text("\n".join(x.text for x in Document(p).paragraphs
                                      if x.text.strip())[:4000])
            except Exception as e:
                st.warning(f"Could not preview: {e}")

# ------------------------------------------------------------------- SEARCH -
with tabs[1]:
    st.subheader("Retrieval only")
    st.caption("Instant. Shows which of the four modes fired and why.")
    q = st.text_input("Query", placeholder="e.g. what skills appear across the resumes")
    k = st.slider("Passages", 1, 20, 6)
    if st.button("Retrieve", disabled=not q.strip()):
        t0 = time.time()
        res = retriever.retrieve(q, k)
        plan = res["plan"]
        st.success(f"mode **{plan.mode}** in {(time.time()-t0)*1000:.0f} ms")
        st.caption(f"Why: {plan.reason}" +
                   (f" \u00b7 anchors: {plan.anchors}" if plan.anchors else ""))

        nodes = res.get("nodes") or []
        if nodes:
            st.markdown("#### Graph nodes reached")
            st.dataframe(pd.DataFrame(nodes), use_container_width=True, hide_index=True)

        for c in res.get("communities") or []:
            with st.expander(f"Theme: {c['title']} ({c['size']} members)"):
                st.write(c["summary"])

        st.markdown("#### Passages")
        for c in res.get("chunks") or []:
            with st.expander(f"[{c.get('doc_id','?')} p.{c.get('page','?')}]"):
                st.text(c["text"][:2500])

        with st.expander("Context as the model receives it"):
            st.code(format_context(res))

# -------------------------------------------------------------------- GRAPH -
with tabs[2]:
    st.subheader("Knowledge graph")
    st.caption("Traversal is what vector search cannot do. Compare hop depths.")
    c1, c2 = st.columns([3, 1])
    ent = c1.text_input("Entity", placeholder="e.g. P-101A, or a person's name")
    hops = c2.slider("Hops", 1, 4, 2)
    if st.button("Traverse", disabled=not ent.strip()):
        hits = store.find(ent.strip())
        if not hits:
            st.warning(f"No entity matching '{ent}'. Try the Search tab to find names.")
        else:
            anchor = hits[0]
            st.write(f"Anchor: **{anchor['name']}** (`{anchor['type']}`)")
            rows = store.neighborhood(anchor["type"], anchor["key"], hops=hops)
            rows = [r for r in rows if r["key"] != anchor["key"]]
            st.write(f"**{len(rows)}** entities within {hops} hop(s)")
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            one = [r for r in store.neighborhood(anchor["type"], anchor["key"], hops=1)
                   if r["key"] != anchor["key"]]
            extra = {r["key"] for r in rows} - {r["key"] for r in one}
            if extra:
                st.info(f"{len(extra)} of these are reachable only beyond one hop \u2014 "
                        "indirect dependencies vector search would miss: "
                        + ", ".join(sorted(r["name"] for r in rows if r["key"] in extra)[:8]))

    with st.expander("Active ontology"):
        st.write(f"**{len(NODE_TYPES)}** node types \u00b7 **{len(REL_PAIRS)}** relations")
        st.dataframe(pd.DataFrame(
            [{"node type": n, "attributes": ", ".join(a)} for n, a in NODE_TYPES.items()]),
            use_container_width=True, hide_index=True)
    with st.expander("Node counts"):
        st.dataframe(pd.DataFrame(sorted(store.stats().items(), key=lambda x: -x[1]),
                                  columns=["type", "count"]),
                     use_container_width=True, hide_index=True)

# ---------------------------------------------------------------- DOCUMENTS -
with tabs[3]:
    st.subheader("Corpus")
    INDEXED = {".pdf", ".txt", ".md"}
    files = sorted(p for p in S.corpus_dir.rglob("*") if p.is_file())
    if files:
        st.dataframe(pd.DataFrame([{
            "file": str(p.relative_to(S.corpus_dir)),
            "KB": round(p.stat().st_size / 1024, 1),
            "indexed": "yes" if p.suffix.lower() in INDEXED else "NO - skipped",
        } for p in files]), use_container_width=True, hide_index=True)
        if any(p.suffix.lower() not in INDEXED for p in files):
            st.warning("Files marked *NO - skipped* are not picked up by `make index`. "
                       "Images are readable on demand via the vision tools, or convert "
                       "them to PDF to have them indexed.")
    else:
        st.info(f"No documents in {S.corpus_dir}")

    up = st.file_uploader("Add documents", type=["pdf", "txt", "md"],
                          accept_multiple_files=True)
    if up:
        for f in up:
            (S.corpus_dir / f.name).write_bytes(f.getbuffer())
        st.success(f"Copied {len(up)} file(s). Stop this app, run `make index`, restart.")

    st.divider()
    st.subheader("Generated artefacts")
    arts = sorted(p for p in S.artifacts_dir.glob("*") if p.is_file())
    if not arts:
        st.info("Nothing generated yet.")
    for p in arts:
        with st.expander(f"{p.name} \u00b7 {p.stat().st_size:,} bytes"):
            st.download_button("Download", p.read_bytes(), p.name, key=f"a-{p.name}")
            try:
                if p.suffix == ".xlsx":
                    st.dataframe(pd.read_excel(p), use_container_width=True)
                elif p.suffix == ".docx":
                    from docx import Document
                    st.text("\n".join(x.text for x in Document(p).paragraphs
                                      if x.text.strip())[:4000])
            except Exception as e:
                st.warning(f"Preview failed: {e}")

# ------------------------------------------------------------------- MODELS -
with tabs[4]:
    st.subheader("Model catalogue")
    st.caption("Adding a model is a block in config/models.yaml \u2014 no code change.")
    st.dataframe(pd.DataFrame([{
        "id": s.id, "tag": s.tag, "VRAM GB": s.vram_gb, "ctx": s.ctx,
        "priority": s.priority, "capabilities": ", ".join(s.capabilities),
        "resident": "yes" if s.id in manager.resident else "",
    } for s in manager.specs.values()]), use_container_width=True, hide_index=True)

    st.write(f"Budget **{manager.budget} GB** \u00b7 resident **{manager.used_gb():.1f} GB**")
    if manager.decisions:
        st.markdown("#### Routing decisions this session")
        st.dataframe(pd.DataFrame([d.__dict__ for d in manager.decisions])[
            ["task", "capability", "tag", "reason", "evicted", "load_ms"]],
            use_container_width=True, hide_index=True)
    else:
        st.info("No model has been invoked yet in this session.")

    st.divider()
    st.subheader("Sandbox check")
    st.caption("Rubric point 3 and half of point 5, verifiable here.")
    code = st.text_area("Python to execute in the sandbox", height=120, value=(
        "import socket\n"
        "socket.setdefaulttimeout(3)\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 53))\n"
        "    print('REACHED INTERNET - sovereignty broken')\n"
        "except Exception as e:\n"
        "    print('blocked:', type(e).__name__, e)\n"))
    if st.button("Execute in sandbox"):
        r = run_python(code)
        st.write(f"backend **{r.backend}** \u00b7 exit {r.exit_code}")
        if r.stdout:
            st.code(r.stdout)
        if r.stderr:
            st.code(r.stderr)
        if "REACHED INTERNET" in r.stdout:
            st.error("Sandbox leaked network access.")
        elif r.ok:
            st.success("Executed with no network reachability.")

# -------------------------------------------------------------------- AUDIT -
with tabs[5]:
    st.subheader("Tamper-evident audit log")
    ok, bad = audit.verify()
    (st.success if ok else st.error)(
        "Hash chain verified" if ok else f"Chain broken at record {bad}")
    n = st.slider("Records", 10, 300, 40)
    recs = audit.tail(n)
    if recs:
        st.dataframe(pd.DataFrame([{
            "time": time.strftime("%H:%M:%S", time.localtime(r["ts"])),
            "event": r["event"],
            "detail": json.dumps(r["payload"], default=str)[:160],
        } for r in reversed(recs)]), use_container_width=True, hide_index=True)
    else:
        st.info("Audit log is empty.")
