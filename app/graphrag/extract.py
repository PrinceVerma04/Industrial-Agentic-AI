"""Chunk -> entities/relations, using the 4B utility model with a constrained
JSON schema. Results are cached by chunk hash: you will re-run indexing dozens
of times during development and re-extraction is the slowest step in the system.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app.graphrag.canonicalize import canonical_key, display_name
from app.graphrag.schema import (EXTRACTION_PROMPT, NODE_TYPES, REL_PAIRS,
                                 extraction_schema, relation_signatures)


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    page: int
    text: str
    bbox: tuple[float, float, float, float] | None = None

    @property
    def provenance(self) -> dict:
        return {"doc_id": self.doc_id, "page": self.page,
                "chunk_id": self.chunk_id, "bbox": self.bbox}


class ExtractionCache:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS cache(h TEXT PRIMARY KEY, payload TEXT)")
        self.db.commit()

    @staticmethod
    def key(text: str, model: str) -> str:
        return hashlib.sha256(f"{model}\x00{text}".encode()).hexdigest()

    def get(self, h: str) -> dict | None:
        row = self.db.execute("SELECT payload FROM cache WHERE h=?", (h,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, h: str, payload: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO cache VALUES(?,?)", (h, json.dumps(payload)))
        self.db.commit()


class Extractor:
    def __init__(self, manager, cache: ExtractionCache):
        self.manager = manager
        self.cache = cache

    def extract(self, chunk: Chunk) -> dict:
        prompt = EXTRACTION_PROMPT.format(
            nodes=", ".join(NODE_TYPES),
            rels=relation_signatures(),
            text=chunk.text[:6000],
        )
        h = self.cache.key(prompt, "utility")
        cached = self.cache.get(h)
        if cached is not None:
            return cached
        raw, _ = self.manager.run_json(
            "extract", [{"role": "user", "content": prompt}], extraction_schema())
        clean = self._validate(raw)
        self.cache.put(h, clean)
        return clean

    @staticmethod
    def _validate(raw: dict) -> dict:
        """Drop anything off-ontology. The model is constrained but not trusted."""
        ents, by_name = [], {}
        for e in raw.get("entities", []):
            t, n = e.get("type"), (e.get("name") or "").strip()
            if t not in NODE_TYPES or not n:
                continue
            key = canonical_key(t, n)
            rec = {"type": t, "key": key, "name": display_name(t, n),
                   "attributes": e.get("attributes") or {}}
            ents.append(rec)
            by_name[n.lower()] = rec

        rels = []
        for r in raw.get("relations", []):
            rt = r.get("type")
            if rt not in REL_PAIRS:
                continue
            src = by_name.get((r.get("source") or "").strip().lower())
            dst = by_name.get((r.get("target") or "").strip().lower())
            if not src or not dst:
                continue                       # endpoints must be declared entities
            if (src["type"], dst["type"]) not in REL_PAIRS[rt]:
                continue                       # endpoint types must match a signature
            rels.append({"type": rt, "source": src, "target": dst,
                         "evidence": (r.get("evidence") or "")[:400]})
        return {"entities": ents, "relations": rels}

    def ingest(self, store, chunks: list[Chunk]) -> dict:
        n_e = n_r = 0
        for c in chunks:
            out = self.extract(c)
            for e in out["entities"]:
                store.upsert_node(e["type"], e["key"], e["name"],
                                  e["attributes"], [c.provenance])
                n_e += 1
            for r in out["relations"]:
                if store.add_rel(r["type"], r["source"]["type"], r["source"]["key"],
                                 r["target"]["type"], r["target"]["key"],
                                 r["evidence"], [c.provenance]):
                    n_r += 1
        return {"entities": n_e, "relations": n_r, "chunks": len(chunks)}
