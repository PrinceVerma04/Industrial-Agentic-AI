"""Hybrid retrieval: dense (bge-m3 via Ollama) + sparse (BM25) + RRF fusion.

Qdrant runs in local on-disk mode - an embedded library, not a server. No port,
no container, nothing to reach from outside the box.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from qdrant_client import QdrantClient, models
from rank_bm25 import BM25Okapi

from app.core.llm import get_client

COLLECTION = "chunks"
DIM = 1024  # bge-m3


def _tok(s: str) -> list[str]:
    return re.findall(r"[a-z0-9\-]+", s.lower())


def point_id(chunk_id: str) -> str:
    """Deterministic ID derived from the chunk, so re-running ingestion
    upserts in place instead of appending a duplicate copy of the corpus."""
    return str(uuid.uuid5(uuid.NAMESPACE_OID, chunk_id))


class VectorIndex:
    def __init__(self, path: Path, embed_model: str = "bge-m3"):
        self.client = QdrantClient(path=str(path))   # embedded, on-disk
        self.embed_model = embed_model
        self.llm = get_client()
        self._ensure()
        self._bm25: BM25Okapi | None = None
        self._corpus: list[dict] = []

    def _ensure(self) -> None:
        names = {c.name for c in self.client.get_collections().collections}
        if COLLECTION not in names:
            self.client.create_collection(
                COLLECTION,
                vectors_config=models.VectorParams(size=DIM, distance=models.Distance.COSINE),
            )

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self.llm.embed(self.embed_model, texts)

    def add(self, chunks: list[dict]) -> int:
        """chunks: [{chunk_id, doc_id, page, text, bbox}]"""
        if not chunks:
            return 0
        vecs = self.embed([c["text"] for c in chunks])
        self.client.upsert(
            COLLECTION,
            points=[
                models.PointStruct(id=point_id(c["chunk_id"]), vector=v, payload=c)
                for c, v in zip(chunks, vecs)
            ],
        )
        self._bm25 = None
        self._corpus = []
        return len(chunks)

    def _count(self) -> int:
        return self.client.count(COLLECTION).count

    def _all(self) -> list[dict]:
        if not self._corpus:
            pts, _ = self.client.scroll(COLLECTION, limit=100_000, with_payload=True)
            self._corpus = [p.payload for p in pts]
        return self._corpus

    def dense(self, query: str, k: int = 10) -> list[dict]:
        qv = self.embed([query])[0]
        hits = self.client.query_points(COLLECTION, query=qv, limit=k).points
        return [{**h.payload, "score": h.score} for h in hits]

    def sparse(self, query: str, k: int = 10) -> list[dict]:
        corpus = self._all()
        if not corpus:
            return []
        if self._bm25 is None:
            self._bm25 = BM25Okapi([_tok(c["text"]) for c in corpus])
        scores = self._bm25.get_scores(_tok(query))
        order = sorted(range(len(scores)), key=lambda i: -scores[i])[:k]
        return [{**corpus[i], "score": float(scores[i])} for i in order]

    def hybrid(self, query: str, k: int = 10, rrf_k: int = 60) -> list[dict]:
        """Reciprocal Rank Fusion. Scale-free, so dense cosine and BM25 scores
        never need calibrating against each other - important for exact tag
        matches ('P-101A'), where BM25 beats embeddings outright."""
        fused: dict[str, dict] = {}
        for lst in (self.dense(query, k * 2), self.sparse(query, k * 2)):
            for rank, item in enumerate(lst):
                cid = item["chunk_id"]
                e = fused.setdefault(cid, {**item, "rrf": 0.0})
                e["rrf"] += 1.0 / (rrf_k + rank + 1)
        return sorted(fused.values(), key=lambda x: -x["rrf"])[:k]
