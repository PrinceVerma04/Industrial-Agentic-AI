"""Embedded property-graph store on Kuzu.

Kuzu is an in-process graph DB - no server, no Docker, no network listener.
On an air-gapped deployment that is a feature, not a compromise: there is no
port for anything to talk to.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import kuzu

from app.graphrag.schema import NODE_TYPES, REL_PAIRS, user_attrs


class GraphStore:
    def __init__(self, path: Path, read_only: bool = False):
        """K\u00f9zu permits one writer but many readers. Anything that only
        queries - the Streamlit console, the API - should open read-only so it
        does not block indexing, and is not blocked by it."""
        path.parent.mkdir(parents=True, exist_ok=True)
        self.read_only = read_only
        if read_only:
            self.db = kuzu.Database(str(path), read_only=True)
            self.conn = kuzu.Connection(self.db)
            return                      # schema already exists; never write
        self.db = kuzu.Database(str(path))
        self.conn = kuzu.Connection(self.db)
        self._init_schema()

    def _exec(self, q: str, params: dict | None = None):
        return self.conn.execute(q, params or {})

    def _init_schema(self) -> None:
        for node in NODE_TYPES:
            cols = "".join(f"{a} STRING, " for a in user_attrs(node))
            self._try(
                f"CREATE NODE TABLE {node}(key STRING, name STRING, {cols}"
                f"provenance STRING, PRIMARY KEY(key))"
            )
        # One table per relation NAME carrying every endpoint pair, so packs
        # that reuse a name (PART_OF in both general and industrial) merge
        # instead of the second definition being silently dropped.
        for rel, pairs in REL_PAIRS.items():
            sig = ", ".join(f"FROM {s} TO {d}" for s, d in pairs)
            self._try(f"CREATE REL TABLE {rel}({sig}, "
                      f"evidence STRING, provenance STRING)")

    def _try(self, q: str) -> None:
        try:
            self._exec(q)
        except RuntimeError as e:
            if "already exists" not in str(e).lower():
                raise

    # ---------- writes ----------
    def upsert_node(self, ntype: str, key: str, name: str,
                    attrs: dict[str, Any] | None = None,
                    provenance: list[dict] | None = None) -> None:
        allowed = set(user_attrs(ntype))
        attrs = {k: str(v) for k, v in (attrs or {}).items() if k in allowed}
        fields = {"key": key, "name": name,
                  "provenance": json.dumps(provenance or []), **attrs}
        for a in allowed:
            fields.setdefault(a, "")
        try:
            self._exec(f"CREATE (n:{ntype} {{{', '.join(f'{k}: ${k}' for k in fields)}}})", fields)
        except RuntimeError:
            sets = ", ".join(f"n.{k} = ${k}" for k in fields if k != "key")
            self._exec(f"MATCH (n:{ntype} {{key: $key}}) SET {sets}", fields)

    def has_rel(self, rtype: str, src_type: str, src_key: str,
                dst_type: str, dst_key: str) -> bool:
        try:
            r = self.rows(
                f"MATCH (a:{src_type} {{key: $sk}})-[r:{rtype}]->(b:{dst_type} {{key: $dk}}) "
                f"RETURN count(r) AS c", {"sk": src_key, "dk": dst_key})
            return bool(r and r[0]["c"])
        except RuntimeError:
            return False

    def add_rel(self, rtype: str, src_type: str, src_key: str,
                dst_type: str, dst_key: str,
                evidence: str = "", provenance: list[dict] | None = None) -> bool:
        """Idempotent: re-running ingestion must not duplicate edges. Kuzu has no
        MERGE for relationships, so existence is checked first."""
        if self.has_rel(rtype, src_type, src_key, dst_type, dst_key):
            return False
        try:
            self._exec(
                f"MATCH (a:{src_type} {{key: $sk}}), (b:{dst_type} {{key: $dk}}) "
                f"CREATE (a)-[:{rtype} {{evidence: $ev, provenance: $pv}}]->(b)",
                {"sk": src_key, "dk": dst_key, "ev": evidence,
                 "pv": json.dumps(provenance or [])},
            )
            return True
        except RuntimeError:
            return False

    # ---------- reads ----------
    def rows(self, q: str, params: dict | None = None) -> list[dict]:
        res = self._exec(q, params)
        out = []
        while res.has_next():
            row = res.get_next()
            out.append({res.get_column_names()[i]: v for i, v in enumerate(row)})
        return out

    def find(self, name_or_tag: str, limit: int = 10) -> list[dict]:
        hits = []
        for ntype in NODE_TYPES:
            hits += self.rows(
                f"MATCH (n:{ntype}) WHERE n.key = $k OR n.name = $k "
                f"RETURN '{ntype}' AS type, n.key AS key, n.name AS name LIMIT {limit}",
                {"k": name_or_tag},
            )
        return hits

    def neighborhood(self, ntype: str, key: str, hops: int = 2, limit: int = 60) -> list[dict]:
        """The core GraphRAG primitive: what is structurally connected to X.
        Vector search cannot answer this - similarity is not connectivity."""
        return self.rows(
            f"MATCH (a:{ntype} {{key: $k}})-[r*1..{hops}]-(b) "
            f"RETURN DISTINCT label(b) AS type, b.key AS key, b.name AS name LIMIT {limit}",
            {"k": key},
        )

    def stats(self) -> dict:
        s = {}
        for n in NODE_TYPES:
            r = self.rows(f"MATCH (n:{n}) RETURN count(n) AS c")
            if r and r[0]["c"]:
                s[n] = r[0]["c"]
        return s
