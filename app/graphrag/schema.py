"""Domain ontology, loaded from config/ontology.yaml.

The ontology is a config artifact, not code. Enable packs with ONTOLOGY= in
.env ("general", or "general,industrial"). A new domain is a new pack in the
YAML - the extractor, store and prompts all derive from it.

Constraining extraction to a closed schema is the biggest quality lever in
GraphRAG: free-form extraction produces a graph where 'Pump 101A', 'P-101A'
and 'the main feed pump' are three unrelated nodes and traversal returns
nothing useful. But the *choice* of schema must follow the corpus, which is
why it is swappable rather than hardcoded.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
RESERVED = {"key", "name", "provenance"}


@lru_cache(maxsize=1)
def _load() -> dict:
    with open(ROOT / "config" / "ontology.yaml") as fh:
        return yaml.safe_load(fh)["packs"]


@lru_cache(maxsize=8)
def build(packs: tuple[str, ...]) -> dict:
    """Merge the enabled packs into one ontology."""
    all_packs = _load()
    unknown = [p for p in packs if p not in all_packs]
    if unknown:
        raise ValueError(f"unknown ontology pack(s) {unknown}; "
                         f"available: {sorted(all_packs)}")

    nodes: dict[str, list[str]] = {}
    tagged: set[str] = set()
    rels: dict[str, list[tuple[str, str]]] = {}

    for name in packs:
        pack = all_packs[name]
        for node, attrs in (pack.get("nodes") or {}).items():
            # Attributes that collide with built-in columns are dropped, not
            # merged: Vendor declaring 'name' would duplicate the key column.
            nodes.setdefault(node, [])
            for a in attrs or []:
                if a not in RESERVED and a not in nodes[node]:
                    nodes[node].append(a)
        tagged |= set(pack.get("tagged") or [])
        for rel, pairs in (pack.get("relations") or {}).items():
            rels.setdefault(rel, [])
            for src, dst in pairs:
                if (src, dst) not in rels[rel]:
                    rels[rel].append((src, dst))

    # A pack may reference a type from another pack (industrial's
    # REQUIRES_APPROVAL_FROM -> Person). Drop pairs whose endpoints are absent
    # rather than failing to start.
    for rel in list(rels):
        rels[rel] = [(s, d) for s, d in rels[rel] if s in nodes and d in nodes]
        if not rels[rel]:
            del rels[rel]

    return {"nodes": nodes, "tagged": tagged & set(nodes), "relations": rels}


def _active() -> dict:
    from app.core.config import get_settings
    packs = tuple(p.strip() for p in get_settings().ontology.split(",") if p.strip())
    return build(packs or ("general",))


class _Lazy(dict):
    """Behaves like the dict it wraps, but resolves from settings on first use
    so importing this module never needs the ontology to be decided yet."""

    def __init__(self, key):
        self._key = key

    def _d(self):
        return _active()[self._key]

    def __getitem__(self, k):       return self._d()[k]
    def __iter__(self):             return iter(self._d())
    def __len__(self):              return len(self._d())
    def __contains__(self, k):      return k in self._d()
    def keys(self):                 return self._d().keys()
    def values(self):               return self._d().values()
    def items(self):                return self._d().items()
    def get(self, k, default=None): return self._d().get(k, default)
    def __repr__(self):             return repr(self._d())


NODE_TYPES = _Lazy("nodes")        # {node: [attrs]}
REL_PAIRS = _Lazy("relations")     # {rel: [(src, dst), ...]}


def tagged_types() -> set[str]:
    return _active()["tagged"]


def rel_types() -> list[tuple[str, str, str]]:
    """Flattened (rel, src, dst) triples."""
    return [(r, s, d) for r, pairs in REL_PAIRS.items() for s, d in pairs]


def user_attrs(ntype: str) -> list[str]:
    return list(NODE_TYPES[ntype])


def extraction_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "entities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": list(NODE_TYPES)},
                        "name": {"type": "string"},
                        "attributes": {"type": "object"},
                    },
                    "required": ["type", "name"],
                },
            },
            "relations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "target": {"type": "string"},
                        "type": {"type": "string", "enum": list(REL_PAIRS)},
                        "evidence": {"type": "string"},
                    },
                    "required": ["source", "target", "type"],
                },
            },
        },
        "required": ["entities", "relations"],
    }


EXTRACTION_PROMPT = """You extract a knowledge graph from documents.

Allowed entity types: {nodes}
Allowed relation types (SOURCE_TYPE -> TARGET_TYPE):
{rels}

Rules:
- Use the entity's exact name as written. Never paraphrase an identifier
  (an asset tag, a DOI, a ticket number, a person's name).
- Only emit a relation if BOTH endpoints appear in your entities list AND the
  endpoint types match the signature above.
- Extract only what the text states. Do not infer or invent.
- If nothing of the allowed types is present, return empty lists.

TEXT:
{text}
"""


def relation_signatures() -> str:
    return "\n".join(
        f"- {rel}: " + ", ".join(f"{s} -> {d}" for s, d in pairs)
        for rel, pairs in REL_PAIRS.items())
