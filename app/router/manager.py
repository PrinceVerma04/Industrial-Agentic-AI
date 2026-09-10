"""VRAM-aware model manager.

On a 16 GB card you cannot hold the reasoning, coding and vision models at
once. This tracks a budget from config/models.yaml, evicts least-recently-used
models before loading one that would overflow, and records every decision so
the UI can prove automatic model selection is happening.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.core.config import get_manifest, get_settings
from app.core.llm import get_client


@dataclass
class ModelSpec:
    id: str
    tag: str
    backend: str
    capabilities: list[str]
    vram_gb: float
    ctx: int
    priority: int = 0


@dataclass
class Decision:
    task: str
    capability: str
    chosen: str
    tag: str
    reason: str
    evicted: list[str] = field(default_factory=list)
    load_ms: int = 0
    ts: float = field(default_factory=time.time)


class ModelManager:
    def __init__(self, profile: str | None = None):
        mf = get_manifest()
        self.profile = profile or get_settings().profile
        prof = mf["profiles"][self.profile]
        self.budget = float(prof["vram_budget_gb"])
        allowed = set(prof["models"])
        self.specs: dict[str, ModelSpec] = {
            m["id"]: ModelSpec(**m) for m in mf["models"] if m["id"] in allowed
        }
        self.tasks: dict[str, str] = mf["tasks"]
        self.task_options: dict[str, dict] = mf.get("task_options", {})
        self.resident: dict[str, float] = {}   # model id -> last used ts
        self.decisions: list[Decision] = []
        self.client = get_client()

    # ---------- selection ----------
    def candidates(self, capability: str) -> list[ModelSpec]:
        c = [s for s in self.specs.values() if capability in s.capabilities]
        return sorted(c, key=lambda s: (-s.priority, s.vram_gb))

    def select(self, task: str) -> tuple[ModelSpec, Decision]:
        cap = self.tasks.get(task, "reason")
        cands = self.candidates(cap)
        if not cands:
            raise LookupError(f"no model in profile '{self.profile}' provides '{cap}'")

        # Prefer an already-resident candidate: avoids a multi-second swap.
        for s in cands:
            if s.id in self.resident:
                d = Decision(task, cap, s.id, s.tag, "resident, capability match")
                self.resident[s.id] = time.time()
                self.decisions.append(d)
                return s, d

        spec = cands[0]
        evicted = self._make_room(spec.vram_gb)
        t0 = time.time()
        self._load(spec)
        d = Decision(
            task, cap, spec.id, spec.tag,
            f"highest-priority model with '{cap}' (fits {spec.vram_gb}GB in {self.budget}GB budget)",
            evicted, int((time.time() - t0) * 1000),
        )
        self.decisions.append(d)
        return spec, d

    # ---------- residency ----------
    def used_gb(self) -> float:
        return sum(self.specs[m].vram_gb for m in self.resident)

    def _make_room(self, need_gb: float) -> list[str]:
        evicted: list[str] = []
        while self.resident and self.used_gb() + need_gb > self.budget:
            lru = min(self.resident, key=self.resident.get)
            self.client.unload(self.specs[lru].tag)
            del self.resident[lru]
            evicted.append(lru)
        return evicted

    def _load(self, spec: ModelSpec) -> None:
        if not self.client.have(spec.tag):
            raise RuntimeError(f"model '{spec.tag}' not pulled. Run: ollama pull {spec.tag}")
        self.resident[spec.id] = time.time()

    # ---------- generation entry point ----------
    def _opts(self, task: str, spec: ModelSpec, kw: dict) -> dict:
        """Task options from the manifest, capped by the model's own context.
        Explicit keyword arguments always win."""
        o = dict(self.task_options.get(task, {}))
        o["num_ctx"] = min(o.get("num_ctx", spec.ctx), spec.ctx)
        o.update(kw)
        return o

    def run(self, task: str, messages: list[dict], **kw) -> tuple[str, Decision]:
        spec, d = self.select(task)
        out = self.client.chat(spec.tag, messages, **self._opts(task, spec, kw))
        return out, d

    def run_json(self, task: str, messages: list[dict], schema: dict, **kw) -> tuple[dict, Decision]:
        spec, d = self.select(task)
        out = self.client.chat_json(spec.tag, messages, schema,
                                    **self._opts(task, spec, kw))
        return out, d


_mgr: ModelManager | None = None


def get_manager() -> ModelManager:
    global _mgr
    if _mgr is None:
        _mgr = ModelManager()
    return _mgr
