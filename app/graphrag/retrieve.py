"""Four retrieval modes, routed by query shape.

The argument for GraphRAG lives here. Vector search answers "what does the SOP
say about pump maintenance". It cannot answer "if we isolate P-101A, which
downstream units and which SOPs are affected" - that is a traversal, and
similarity is not connectivity.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.graphrag.canonicalize import normalize_tag

MODE_VECTOR = "vector"      # specific fact
MODE_LOCAL = "local"        # entity-anchored neighbourhood
MODE_GLOBAL = "global"      # corpus-wide themes via community summaries
MODE_TRAVERSE = "traverse"  # multi-hop / impact analysis

_GLOBAL = re.compile(
    r"\b(overall|across|themes?|trends?|recurring|common|patterns?|"
    r"how many|most (common|frequent)|summar(y|ise|ize) all)\b", re.I)
_IMPACT = re.compile(
    r"\b(if we|impact|affect(ed|s)?|downstream|upstream|depend|"
    r"isolat(e|ion)|shut ?down|trip|knock ?on|consequence)\b", re.I)
_TAGISH = re.compile(r"\b[A-Z]{1,4}[\s\-_]?\d{2,5}[A-Z]?\b")


@dataclass
class RetrievalPlan:
    mode: str
    anchors: list[str]
    reason: str


def plan(query: str) -> RetrievalPlan:
    tags = [t for t in (normalize_tag(m.group()) for m in _TAGISH.finditer(query.upper())) if t]
    if _IMPACT.search(query) and tags:
        return RetrievalPlan(MODE_TRAVERSE, tags, "impact language + explicit tag -> multi-hop")
    if _GLOBAL.search(query):
        return RetrievalPlan(MODE_GLOBAL, [], "corpus-wide language -> community summaries")
    if tags:
        return RetrievalPlan(MODE_LOCAL, tags, "explicit tag -> entity neighbourhood")
    return RetrievalPlan(MODE_VECTOR, [], "no structural signal -> hybrid vector search")


class GraphRetriever:
    def __init__(self, store, index, manager):
        self.store, self.index, self.manager = store, index, manager

    def _chunks(self, query: str, k: int) -> list[dict]:
        """Text retrieval is best-effort. If the embedding model is not pulled
        yet, graph traversal still answers structural questions on its own -
        degrade, never fail."""
        try:
            return self.index.hybrid(query, k)
        except Exception:
            return []

    def retrieve(self, query: str, k: int = 8) -> dict:
        p = plan(query)
        if p.mode == MODE_VECTOR:
            return {"plan": p, "chunks": self._chunks(query, k), "nodes": []}
        if p.mode == MODE_LOCAL:
            return {"plan": p, **self._local(query, p.anchors, k, hops=1)}
        if p.mode == MODE_TRAVERSE:
            return {"plan": p, **self._local(query, p.anchors, k, hops=3)}
        return {"plan": p, **self._global(query, k)}

    def _local(self, query: str, anchors: list[str], k: int, hops: int) -> dict:
        nodes: list[dict] = []
        for tag in anchors:
            for hit in self.store.find(tag):
                nodes.append(hit)
                nodes += self.store.neighborhood(hit["type"], hit["key"], hops=hops)
        seen, uniq = set(), []
        for n in nodes:
            if n["key"] not in seen:
                seen.add(n["key"])
                uniq.append(n)
        # Ground the subgraph in text: search using the entity names we found.
        expanded = query + " " + " ".join(n["name"] for n in uniq[:15])
        return {"nodes": uniq, "chunks": self._chunks(expanded, k)}

    def _global(self, query: str, k: int) -> dict:
        from app.graphrag.communities import load_summaries
        summaries = load_summaries(self.store)
        if not summaries:
            return {"nodes": [], "chunks": self._chunks(query, k)}
        scored = self._chunks(query, k)
        return {"nodes": [], "chunks": scored, "communities": summaries[:5]}


def format_context(result: dict, max_chars: int = 6000) -> str:
    """Render retrieval output as grounded, citable context."""
    out = []
    if result.get("nodes"):
        out.append("## Knowledge graph (structurally connected entities)")
        for n in result["nodes"][:25]:
            out.append(f"- [{n['type']}] {n['name']} ({n['key']})")
    for c in result.get("communities", []) or []:
        out.append(f"\n## Theme: {c['title']}\n{c['summary']}")
    if result.get("chunks"):
        out.append("\n## Source passages")
        for c in result["chunks"]:
            cite = f"[{c.get('doc_id','?')} p.{c.get('page','?')}]"
            out.append(f"{cite} {c['text'][:900]}")
    return "\n".join(out)[:max_chars]
