"""Ingest data/corpus -> vector index + knowledge graph + community summaries.

Safe to re-run: extraction is cached by chunk hash, so only new or changed
content costs LLM time.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rich.console import Console

from app.core.config import get_settings
from app.graphrag.communities import CommunityStore, summarise_all
from app.graphrag.extract import ExtractionCache, Extractor
from app.graphrag.store import GraphStore
from app.ingest.pipeline import ingest_dir
from app.retrieval.vector import VectorIndex
from app.router.manager import get_manager

c = Console()


def main(skip_communities: bool = False) -> None:
    s = get_settings()
    chunks = ingest_dir(s.corpus_dir)
    c.print(f"[bold]{len(chunks)}[/bold] chunks from {s.corpus_dir}")
    if not chunks:
        c.print("[yellow]No documents in data/corpus - add .pdf/.txt/.md first.[/]")
        return

    try:
        index = VectorIndex(s.stores_dir / "qdrant")
        n = index.add([{"chunk_id": ch.chunk_id, "doc_id": ch.doc_id, "page": ch.page,
                        "text": ch.text, "bbox": ch.bbox} for ch in chunks])
        c.print(f"indexed [bold]{n}[/bold] chunks into vector store")
    except Exception as e:
        c.print(f"[yellow]vector index skipped ({type(e).__name__}: {str(e)[:80]}).[/]")
        c.print("[yellow]Pull the embedding model:  ollama pull bge-m3[/]")

    store = GraphStore(s.stores_dir / "graph")
    ex = Extractor(get_manager(), ExtractionCache(s.stores_dir / "extract_cache.db"))
    with c.status("extracting knowledge graph..."):
        stats = ex.ingest(store, chunks)
    c.print(f"graph: {stats}")
    c.print(f"node counts: {store.stats()}")

    if not skip_communities:
        with c.status("detecting communities + summarising..."):
            k = summarise_all(store, get_manager(),
                              CommunityStore(s.stores_dir / "communities.db"))
        c.print(f"summarised [bold]{k}[/bold] communities")


if __name__ == "__main__":
    main(skip_communities="--no-communities" in sys.argv)
