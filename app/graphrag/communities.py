"""Leiden community detection + LLM summaries.

This is what makes global search possible: "what are the recurring failure
themes across five years of inspection reports" has no single source passage,
so vector search structurally cannot answer it. Instead we cluster the graph,
summarise each cluster once at index time, and search over those summaries.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import networkx as nx

from app.graphrag.schema import NODE_TYPES, rel_types

SUMMARY_PROMPT = """You are summarising one cluster of a knowledge graph.

Entities in this cluster:
{entities}

Observed relationships, written as "SOURCE RELATION TARGET". The direction is
significant - "A PART_OF B" means A is a component of B, never the reverse.
Restate each relationship in the same direction it is given:
{relations}

Write:
1. A short title (max 8 words).
2. A 3-5 sentence summary of what this cluster represents, and any dependencies,
   themes or risks it implies.

Only use the facts given. Do not speculate."""

_SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}, "summary": {"type": "string"}},
    "required": ["title", "summary"],
}


def build_networkx(store) -> nx.DiGraph:
    """DIRECTED on purpose. "V-4401 PART_OF P-101A" and "P-101A PART_OF V-4401"
    are opposite operational claims; collapsing them into an undirected edge
    made the LLM summaries state relationships backwards. Clustering converts
    to undirected separately - only the rendering needs direction."""
    g = nx.DiGraph()
    for ntype in NODE_TYPES:
        for r in store.rows(f"MATCH (n:{ntype}) RETURN n.key AS key, n.name AS name"):
            g.add_node(r["key"], name=r["name"], type=ntype)
    for rel, src, dst in rel_types():
        try:
            rows = store.rows(
                f"MATCH (a:{src})-[r:{rel}]->(b:{dst}) RETURN a.key AS s, b.key AS t")
        except RuntimeError:
            continue
        for r in rows:
            g.add_edge(r["s"], r["t"], type=rel)
    return g


def detect(g: nx.DiGraph, resolution: float = 1.0) -> dict[int, list[str]]:
    """Leiden if python-igraph is present, else NetworkX greedy modularity.
    Both give usable clusters; Leiden is better on large sparse graphs."""
    if g.number_of_nodes() == 0:
        return {}
    ug = g.to_undirected(as_view=False)   # community detection is undirected
    try:
        import igraph as ig
        import leidenalg

        nodes = list(ug.nodes())
        idx = {n: i for i, n in enumerate(nodes)}
        h = ig.Graph(n=len(nodes), edges=[(idx[u], idx[v]) for u, v in ug.edges()])
        part = leidenalg.find_partition(
            h, leidenalg.RBConfigurationVertexPartition, resolution_parameter=resolution)
        return {i: [nodes[j] for j in c] for i, c in enumerate(part)}
    except ImportError:
        comms = nx.community.greedy_modularity_communities(ug)
        return {i: list(c) for i, c in enumerate(comms)}


class CommunityStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS communities"
            "(cid INTEGER PRIMARY KEY, title TEXT, summary TEXT, members TEXT, size INT)")
        self.db.commit()

    def put(self, cid: int, title: str, summary: str, members: list[str]) -> None:
        self.db.execute("INSERT OR REPLACE INTO communities VALUES(?,?,?,?,?)",
                        (cid, title, summary, json.dumps(members), len(members)))
        self.db.commit()

    def all(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT cid,title,summary,members,size FROM communities ORDER BY size DESC").fetchall()
        return [{"cid": r[0], "title": r[1], "summary": r[2],
                 "members": json.loads(r[3]), "size": r[4]} for r in rows]


def summarise_all(store, manager, cstore: CommunityStore, min_size: int = 3) -> int:
    g = build_networkx(store)
    comms = detect(g)
    n = 0
    for cid, members in comms.items():
        if len(members) < min_size:
            continue
        ents = "\n".join(f"- [{g.nodes[m]['type']}] {g.nodes[m]['name']}" for m in members[:40])
        sub = g.subgraph(members)          # DiGraph view: direction preserved
        rels = "\n".join(
            f"- {g.nodes[u]['name']} {d['type']} {g.nodes[v]['name']}"
            for u, v, d in sub.edges(data=True))[:3000]
        out, _ = manager.run_json(
            "summarize",
            [{"role": "user", "content": SUMMARY_PROMPT.format(entities=ents, relations=rels)}],
            _SCHEMA)
        cstore.put(cid, out["title"], out["summary"], members)
        n += 1
    return n


_cache: list[dict] | None = None


def load_summaries(store) -> list[dict]:
    from app.core.config import get_settings
    global _cache
    if _cache is None:
        _cache = CommunityStore(get_settings().stores_dir / "communities.db").all()
    return _cache
