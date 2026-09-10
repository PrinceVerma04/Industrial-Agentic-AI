"""Thin Ollama client. Single choke point for every model call in the system.

Deliberately not the `openai` SDK: that library will attempt DNS on misconfig,
and on an air-gapped box we want one auditable transport pinned to localhost.
"""
from __future__ import annotations

import json
from typing import Any, Iterator

import httpx

from app.core.config import get_settings


class OllamaClient:
    def __init__(self, host: str | None = None, timeout: float = 600.0):
        self.host = (host or get_settings().ollama_host).rstrip("/")
        if not self.host.startswith(("http://127.0.0.1", "http://localhost")):
            raise ValueError(f"refusing non-local LLM host: {self.host}")
        self._c = httpx.Client(base_url=self.host, timeout=timeout)

    # ---------- introspection ----------
    def list_models(self) -> list[dict]:
        return self._c.get("/api/tags").json().get("models", [])

    def loaded(self) -> list[dict]:
        return self._c.get("/api/ps").json().get("models", [])

    def have(self, tag: str) -> bool:
        names = {m["name"] for m in self.list_models()}
        return tag in names or f"{tag}:latest" in names

    # ---------- generation ----------
    def chat(
        self,
        model: str,
        messages: list[dict],
        *,
        images: list[str] | None = None,
        fmt: dict | str | None = None,
        temperature: float = 0.2,
        num_ctx: int | None = None,
        num_predict: int | None = None,
        think: bool | None = None,
        keep_alive: str = "5m",
    ) -> str:
        if images:
            messages = [dict(m) for m in messages]
            messages[-1]["images"] = images
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "keep_alive": keep_alive,
            "options": {"temperature": temperature},
        }
        if num_ctx:
            body["options"]["num_ctx"] = num_ctx
        if num_predict:
            # Hard ceiling. Without it a model asked for JSON can emit
            # thousands of tokens of prose and stall the whole request.
            body["options"]["num_predict"] = num_predict
        if think is not None:
            # Reasoning tokens are discarded when output is schema-constrained,
            # yet they dominate latency (~73% of a call on qwen3:14b).
            body["think"] = think
        if fmt:
            body["format"] = fmt  # JSON-schema constrained decoding
        r = self._c.post("/api/chat", json=body)
        r.raise_for_status()
        return r.json()["message"]["content"]

    def chat_json(self, model: str, messages: list[dict], schema: dict, **kw) -> dict:
        """Schema-constrained generation. Used everywhere structure matters
        (routing, graph extraction) so we never regex-parse model prose."""
        raw = self.chat(model, messages, fmt=schema, temperature=0.0, **kw)
        return json.loads(raw)

    def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        r = self._c.post("/api/embed", json={"model": model, "input": texts})
        r.raise_for_status()
        return r.json()["embeddings"]

    def unload(self, model: str) -> None:
        """Evict a model from VRAM immediately (keep_alive=0)."""
        try:
            self._c.post("/api/generate", json={"model": model, "keep_alive": 0})
        except httpx.HTTPError:
            pass


_client: OllamaClient | None = None


def get_client() -> OllamaClient:
    global _client
    if _client is None:
        _client = OllamaClient()
    return _client
