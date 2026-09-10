"""Workspace-jailed file access. Every path is resolved and checked to be
inside the workspace root, so a traversal like ../../etc/passwd cannot escape."""
from __future__ import annotations

from pathlib import Path

from app.core.config import get_settings

MAX_READ = 400_000


def _root() -> Path:
    return get_settings().workspace_dir.resolve()


def _safe(rel: str) -> Path:
    p = (_root() / rel).resolve()
    if p != _root() and _root() not in p.parents:
        raise PermissionError(f"path escapes workspace: {rel}")
    return p


def read_file(path: str) -> str:
    p = _safe(path)
    if not p.is_file():
        raise FileNotFoundError(path)
    return p.read_text(errors="replace")[:MAX_READ]


def write_file(path: str, content: str) -> dict:
    p = _safe(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return {"path": str(p.relative_to(_root())), "bytes": len(content.encode())}


def list_files(subdir: str = ".") -> list[str]:
    base = _safe(subdir)
    if not base.exists():
        return []
    return sorted(str(p.relative_to(_root())) for p in base.rglob("*") if p.is_file())
