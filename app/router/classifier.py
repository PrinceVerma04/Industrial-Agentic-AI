"""Two-stage task router.

Stage 1 is pure heuristics and costs nothing - it resolves the large majority
of requests. Stage 2 falls back to a 4B utility model with schema-constrained
output, and only runs when the heuristics are not confident. Keeping the LLM
off the hot path is what makes routing feel instant in a live demo.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

TASKS = ["code", "vision", "extract", "reason", "plan", "doc", "summarize"]

_CODE = re.compile(
    r"```|\b(def |class |import |func |SELECT |npm |pip |traceback|stack ?trace|"
    r"write (a |some )?(python|js|bash|sql)|debug|refactor|unit test)\b", re.I)
_DOC = re.compile(
    r"\b(approval note|draft (a|an)|memo|report|presentation|deck|slide|"
    r"word file|docx|pptx|xlsx|spreadsheet|letter|minutes)\b", re.I)
_SUM = re.compile(r"\b(summari[sz]e|tl;?dr|key (points|findings)|brief|digest)\b", re.I)
_PLAN = re.compile(r"\b(plan|steps|workflow|procedure|checklist|how do i|walk me through)\b", re.I)
_EXTRACT = re.compile(r"\b(extract|list all|pull out|parse|tabulate|fields|entities)\b", re.I)

_SCHEMA = {
    "type": "object",
    "properties": {"task": {"type": "string", "enum": TASKS}},
    "required": ["task"],
}


@dataclass
class Route:
    task: str
    confidence: float
    signals: list[str]
    stage: str


def classify(prompt: str, *, has_image: bool = False, has_doc: bool = False) -> Route:
    sig: list[str] = []

    if has_image:
        return Route("vision", 1.0, ["image attachment"], "heuristic")
    if has_doc:
        sig.append("document attachment")

    if _CODE.search(prompt):
        sig.append("code tokens")
        return Route("code", 0.9, sig, "heuristic")
    if _DOC.search(prompt):
        sig.append("deliverable keywords")
        return Route("doc", 0.85, sig, "heuristic")
    if _EXTRACT.search(prompt):
        sig.append("extraction verbs")
        return Route("extract", 0.8, sig, "heuristic")
    if _SUM.search(prompt):
        sig.append("summarisation verbs")
        return Route("summarize", 0.8, sig, "heuristic")
    if _PLAN.search(prompt):
        sig.append("planning verbs")
        return Route("plan", 0.75, sig, "heuristic")

    # Short factual questions do not need the big model.
    if len(prompt.split()) < 12 and prompt.strip().endswith("?"):
        sig.append("short question")
        return Route("summarize", 0.6, sig, "heuristic")

    return Route("reason", 0.4, sig + ["no strong signal"], "heuristic-fallback")


def classify_llm(prompt: str, manager) -> Route:
    """Stage 2. Only called when stage 1 confidence is low."""
    out, _ = manager.run_json(
        "classify",
        [{"role": "user",
          "content": f"Classify this request into exactly one task type.\n\n{prompt[:1500]}"}],
        _SCHEMA,
    )
    return Route(out["task"], 0.7, ["utility-model classification"], "llm")


def route(prompt: str, manager=None, *, has_image=False, has_doc=False,
          threshold: float = 0.5) -> Route:
    r = classify(prompt, has_image=has_image, has_doc=has_doc)
    if r.confidence < threshold and manager is not None:
        try:
            return classify_llm(prompt, manager)
        except Exception:
            return r  # never let routing failure block the request
    return r
