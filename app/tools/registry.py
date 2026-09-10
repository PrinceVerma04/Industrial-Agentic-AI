"""Tool registry. Each tool declares a JSON schema; the agent only ever sees
these declarations, and every invocation is written to the audit log."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

_REGISTRY: dict[str, "Tool"] = {}


def _split(text: str) -> list[str]:
    """A model asked for a list often returns prose. Recover the items."""
    parts = re.split(r"\n\s*(?:[-*\u2022]|\d+[.)])\s*|\n{2,}|;\s*", text.strip())
    return [p.strip(" -*\u2022\t") for p in parts if p and p.strip(" -*\u2022\t")]


def coerce_args(schema: dict, args: dict) -> tuple[dict, list[str]]:
    """Best-effort coercion of planner-supplied arguments to the declared
    schema. Planners routinely emit a string where an array is wanted, or a
    list of strings where a list of objects is wanted; failing the call over
    that wastes an entire agent iteration. Unknown keys are dropped and
    missing required keys are reported rather than raising TypeError."""
    props = schema.get("properties", {})
    out, notes = {}, []

    for k, v in (args or {}).items():
        if k not in props:
            notes.append(f"dropped unknown argument '{k}'")
            continue
        want = props[k].get("type")
        try:
            if want == "array" and not isinstance(v, list):
                v = _split(v) if isinstance(v, str) else [v]
                notes.append(f"coerced '{k}' to array")
            if want == "array" and props[k].get("items", {}).get("type") == "object":
                v = [x if isinstance(x, dict) else {"text": str(x)} for x in v]
            if want == "array" and props[k].get("items", {}).get("type") == "array":
                v = [x if isinstance(x, list) else [x] for x in v]
            if want == "object" and isinstance(v, str):
                v = json.loads(v)
                notes.append(f"parsed '{k}' from JSON string")
            if want == "integer" and isinstance(v, str) and v.strip().lstrip("-").isdigit():
                v = int(v)
            if want == "string" and isinstance(v, (list, dict)):
                v = json.dumps(v) if isinstance(v, dict) else "\n".join(map(str, v))
                notes.append(f"flattened '{k}' to string")
        except Exception as e:
            notes.append(f"could not coerce '{k}': {e}")
        out[k] = v

    missing = [r for r in schema.get("required", []) if r not in out]
    if missing:
        notes.append(f"MISSING REQUIRED: {missing}")
    return out, notes


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable[..., Any]
    dangerous: bool = False


def tool(name: str, description: str, parameters: dict, dangerous: bool = False):
    def deco(fn):
        _REGISTRY[name] = Tool(name, description, parameters, fn, dangerous)
        return fn
    return deco


def call(name: str, args: dict) -> Any:
    """Single invocation path: coerce, then call. Every agent tool call goes
    through here so coercion is never bypassed."""
    t = get(name)
    clean, notes = coerce_args(t.parameters, args)
    missing = [n for n in notes if n.startswith("MISSING REQUIRED")]
    if missing:
        raise ValueError(f"{missing[0]} for tool '{name}'. "
                         f"Provide them and call the tool again.")
    return t.fn(**clean)


def get(name: str) -> Tool:
    if name not in _REGISTRY:
        raise KeyError(f"unknown tool: {name}")
    return _REGISTRY[name]


def all_tools() -> list[Tool]:
    return list(_REGISTRY.values())


def specs() -> list[dict]:
    return [{"name": t.name, "description": t.description, "parameters": t.parameters}
            for t in _REGISTRY.values()]


def _s(**props):
    return {"type": "object", "properties": props, "required": list(props)}


def register_all(ctx: dict) -> None:
    """ctx carries the live store/index/retriever so tools stay stateless."""
    from app.tools import (calc as _calc, docgen as _doc, fs as _fs,
                           sandbox as _sbx, vision as _vis)
    from app.graphrag.retrieve import format_context

    @tool("read_file", "Read a text file from the workspace.",
          _s(path={"type": "string"}))
    def _rf(path): return _fs.read_file(path)

    @tool("write_file", "Write a text file into the workspace.",
          _s(path={"type": "string"}, content={"type": "string"}))
    def _wf(path, content): return _fs.write_file(path, content)

    @tool("list_files",
          "List files in the workspace. Returns a bare JSON array of path "
          "strings, e.g. [\"a.txt\", \"sub/b.pdf\"] -- NOT an object with a "
          "'files' key. Reference an entry with {{stepN[0]}}, not {{stepN['files'][0]}}.",
          {"type": "object", "properties": {"subdir": {"type": "string"}}, "required": []})
    def _lf(subdir="."): return _fs.list_files(subdir)

    @tool("run_python", "Execute Python in a network-isolated sandbox. Returns stdout/stderr.",
          _s(code={"type": "string"}), dangerous=True)
    def _rp(code):
        r = _sbx.run_python(code)
        return {"ok": r.ok, "stdout": r.stdout, "stderr": r.stderr,
                "backend": r.backend, "files": r.files}

    @tool("calculate", "Symbolic/numeric calculation that returns the full derivation steps.",
          {"type": "object",
           "properties": {"expression": {"type": "string"},
                          "variables": {"type": "object"},
                          "solve_for": {"type": "string"}},
           "required": ["expression"]})
    def _ca(expression, variables=None, solve_for=None):
        return _calc.evaluate(expression, variables, solve_for)

    @tool("kb_search", "Search the local knowledge base. Automatically chooses vector, "
                       "graph-neighbourhood, multi-hop traversal or global-theme retrieval.",
          {"type": "object",
           "properties": {"query": {"type": "string"}, "k": {"type": "integer"}},
           "required": ["query"]})
    def _kb(query, k=8):
        res = ctx["retriever"].retrieve(query, k)
        return {"mode": res["plan"].mode, "why": res["plan"].reason,
                "context": format_context(res),
                "citations": [{"doc_id": c.get("doc_id"), "page": c.get("page")}
                              for c in res.get("chunks", [])]}

    @tool("graph_query", "Query the knowledge graph directly: find an entity or walk its "
                         "neighbourhood N hops. Use for impact and dependency questions.",
          {"type": "object",
           "properties": {"entity": {"type": "string"}, "hops": {"type": "integer"}},
           "required": ["entity"]})
    def _gq(entity, hops=2):
        store = ctx["store"]
        hits = store.find(entity)
        if not hits:
            return {"found": False, "entity": entity}
        h = hits[0]
        return {"found": True, "anchor": h,
                "neighbourhood": store.neighborhood(h["type"], h["key"], hops=hops)}

    @tool("ocr_document", "Extract exact text from a scanned PDF or image using local "
                          "CPU OCR. Use for printed documents.",
          {"type": "object",
           "properties": {"path": {"type": "string"}, "max_pages": {"type": "integer"}},
           "required": ["path"]})
    def _ocr(path, max_pages=4):
        r = _vis.ocr_document(path, max_pages)
        return {"file": r["file"], "engine": r["engine"], "text": r["text"][:8000]}

    @tool("describe_document", "Understand a scanned drawing, P&ID, photograph or "
                               "handwritten note with the local vision model. Use when "
                               "layout, symbols or handwriting matter.",
          {"type": "object",
           "properties": {"path": {"type": "string"}, "question": {"type": "string"}},
           "required": ["path"]})
    def _vlm(path, question=""):
        return _vis.describe_document(path, question)

    @tool("make_approval_note", "Generate a formal approval note as a .docx file.",
          {"type": "object",
           "properties": {"title": {"type": "string"}, "subject": {"type": "string"},
                          "background": {"type": "string"},
                          "findings": {"type": "array", "items": {"type": "string"}},
                          "recommendation": {"type": "string"},
                          "citations": {"type": "array", "items": {"type": "object"}},
                          "filename": {"type": "string"}},
           "required": ["title", "subject", "background", "findings", "recommendation"]})
    def _an(**kw): return {"file": _doc.approval_note(**kw)}

    @tool("make_deck", "Generate a .pptx presentation.",
          {"type": "object",
           "properties": {"title": {"type": "string"},
                          "slides": {"type": "array", "items": {"type": "object"}},
                          "filename": {"type": "string"}},
           "required": ["title", "slides"]})
    def _dk(**kw): return {"file": _doc.deck(**kw)}

    @tool("make_sheet", "Generate an .xlsx spreadsheet.",
          {"type": "object",
           "properties": {"rows": {"type": "array", "items": {"type": "array"}},
                          "headers": {"type": "array", "items": {"type": "string"}},
                          "filename": {"type": "string"}},
           "required": ["rows"]})
    def _sh(**kw): return {"file": _doc.sheet(**kw)}
