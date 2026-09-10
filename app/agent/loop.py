"""Plan -> Execute -> Critique agent loop.

Deliberately hand-written rather than a framework: ~200 lines, no hidden
network calls, and every step is auditable - which is the whole point of a
sovereign deployment. The planner emits a typed step list; the executor runs
tools; the critic decides whether the goal is met or another round is needed.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from app.core.audit import AuditLog
from app.tools import registry
from app.tools.render import render

_FAILED = object()   # sentinel: this step errored, its output is not usable

# {{step1}}, {{ step_2 }}, {{step2['rows']}}, {{step3[0]['name']}}
_STEP_REF = re.compile(
    r"\{\{\s*step[_ ]?(\d+)((?:\s*\[[^\]]*\])*)\s*(\|\s*json)?\s*\}\}")
_ANY_BRACE = re.compile(r"\{\{[^}]*\}\}")

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "intent": {"type": "string"},
                    "tool": {"type": "string"},
                    "arguments": {"type": "object"},
                },
                "required": ["id", "intent", "tool", "arguments"],
            },
        },
    },
    "required": ["reasoning", "steps"],
}

CRITIC_SCHEMA = {
    "type": "object",
    "properties": {
        "goal_met": {"type": "boolean"},
        "assessment": {"type": "string"},
        "missing": {"type": "string"},
    },
    "required": ["goal_met", "assessment"],
}


@dataclass
class StepResult:
    id: int
    intent: str
    tool: str
    arguments: dict
    ok: bool
    output: Any
    ms: int


@dataclass
class RunResult:
    goal: str
    plan_reasoning: str
    steps: list[StepResult] = field(default_factory=list)
    answer: str = ""
    iterations: int = 0
    decisions: list[dict] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)


class Agent:
    def __init__(self, manager, audit: AuditLog, max_iterations: int = 3,
                 max_steps: int = 8):
        self.manager = manager
        self.audit = audit
        self.max_iterations = max_iterations
        self.max_steps = max_steps

    # ---------- planning ----------
    def _plan(self, goal: str, history: str = "") -> dict:
        tools_desc = "\n".join(
            f"- {t['name']}({', '.join(t['parameters'].get('properties', {}))}): {t['description']}"
            for t in registry.specs())
        prompt = (
            f"You are an on-premise industrial assistant. Break the goal into concrete "
            f"tool calls. Use at most {self.max_steps} steps. Prefer kb_search or "
            f"graph_query before answering from memory - you must ground claims in the "
            f"organisation's own documents.\n\n"
            f"IMPORTANT - passing data between steps: you are writing all steps "
            f"BEFORE any of them run, so you cannot know their results. Reference an "
            f"earlier step's output with the placeholder {{{{step1}}}} inside a string "
            f"argument; it is replaced with that step's real output at execution time. "
            f"You may subscript it, e.g. {{{{step2['rows']}}}}.\n"
            f"INSIDE run_python CODE you MUST use the json form, e.g. "
            f"data = {{{{step1|json}}}}  -- this expands to a valid Python literal. "
            f"Using the plain form inside code produces a SyntaxError, because it "
            f"expands to human-readable prose, not a literal.\n"
            f"NEVER invent placeholder content such as 'Equipment X, Y, Z'.\n\n"
            f"AVAILABLE TOOLS:\n{tools_desc}\n\nGOAL: {goal}\n"
            + (f"\nPREVIOUS ATTEMPT:\n{history}\n" if history else "")
        )
        plan, dec = self.manager.run_json("plan", [{"role": "user", "content": prompt}],
                                          PLAN_SCHEMA)
        self.audit.append("plan", goal=goal, model=dec.tag, steps=len(plan.get("steps", [])))
        return plan

    # ---------- execution ----------
    @staticmethod
    def _resolve(value, results: dict[int, Any]):
        """Substitute {{stepN}} references with the output of step N.

        Also handles subscripted forms the planner reaches for naturally,
        e.g. {{step2['rows']}}. Anything that cannot be resolved becomes an
        explicit marker: an unsubstituted placeholder must never survive into
        a generated document, and neither must a failed step's error text.
        """
        if isinstance(value, str):
            def sub(m):
                got = results.get(int(m.group(1)))
                if got is None:
                    return "[step not run]"
                if got is _FAILED:
                    return "[data unavailable - upstream step failed]"
                for key in re.findall(r"\[([^\]]*)\]", m.group(2) or ""):
                    key = key.strip().strip("'\"")
                    try:
                        got = got[int(key)] if key.lstrip("-").isdigit() else got[key]
                    except (KeyError, IndexError, TypeError):
                        return f"[no '{key}' in step {m.group(1)} output]"
                if m.group(3):          # |json -> a valid Python/JSON literal
                    return json.dumps(got, default=str)
                return render(got)

            out = _STEP_REF.sub(sub, value)
            # Safety net: any placeholder shape we failed to recognise is
            # blanked rather than printed verbatim into a deliverable.
            return _ANY_BRACE.sub("[unresolved reference]", out)
        if isinstance(value, list):
            return [Agent._resolve(v, results) for v in value]
        if isinstance(value, dict):
            return {k: Agent._resolve(v, results) for k, v in value.items()}
        return value

    def _execute(self, steps: list[dict]) -> list[StepResult]:
        out: list[StepResult] = []
        results: dict[int, Any] = {}
        for s in steps[: self.max_steps]:
            s = dict(s)
            s["arguments"] = self._resolve(s.get("arguments") or {}, results)
            t0 = time.time()
            try:
                result = registry.call(s["tool"], s.get("arguments") or {})
                # A tool may report failure by RETURNING {"ok": False} rather
                # than raising - run_python does exactly this. Without this
                # check the step is marked successful and its error text is
                # substitutable into a deliverable.
                ok = not (isinstance(result, dict) and result.get("ok") is False)
            except Exception as e:
                result, ok = f"{type(e).__name__}: {e}", False
            ms = int((time.time() - t0) * 1000)
            self.audit.append("tool_call", tool=s.get("tool"),
                              arguments=s.get("arguments"), ok=ok, ms=ms)
            # Only successful results are substitutable. Injecting a stack
            # trace into an approval note is worse than omitting the content.
            results[s.get("id", len(results) + 1)] = (
                result if ok else _FAILED)
            out.append(StepResult(s.get("id", 0), s.get("intent", ""), s.get("tool", ""),
                                  s.get("arguments") or {}, ok, result, ms))
        return out

    # ---------- critique ----------
    def _critique(self, goal: str, results: list[StepResult]) -> dict:
        trace = "\n".join(
            f"[{r.id}] {r.tool} ok={r.ok} -> {render(r.output)[:600]}" for r in results)
        out, _ = self.manager.run_json(
            "reason",
            [{"role": "user",
              "content": f"GOAL: {goal}\n\nEXECUTION TRACE:\n{trace}\n\n"
                         f"Has the goal been fully achieved? If not, state precisely "
                         f"what is missing."}],
            CRITIC_SCHEMA)
        self.audit.append("critique", goal_met=out["goal_met"])
        return out

    # ---------- synthesis ----------
    def _answer(self, goal: str, results: list[StepResult]) -> str:
        trace = "\n\n".join(
            f"### {r.tool} ({r.intent})\n{render(r.output)[:2500]}" for r in results if r.ok)
        out, _ = self.manager.run(
            "reason",
            [{"role": "user",
              "content": f"Answer the request using ONLY the tool results below. "
                         f"Cite sources as [doc_id p.N] where available. If a document "
                         f"was generated, state its path.\n\n"
                         f"REQUEST: {goal}\n\nTOOL RESULTS:\n{trace}"}])
        return out

    # ---------- entry point ----------
    def run(self, goal: str) -> RunResult:
        res = RunResult(goal=goal, plan_reasoning="")
        history = ""
        for i in range(self.max_iterations):
            res.iterations = i + 1
            plan = self._plan(goal, history)
            res.plan_reasoning = plan.get("reasoning", "")
            steps = self._execute(plan.get("steps", []))
            res.steps.extend(steps)
            # On the final permitted iteration the critic's verdict cannot
            # trigger a replan, so running it is a wasted LLM call.
            if i == self.max_iterations - 1:
                break
            crit = self._critique(goal, steps)
            if crit["goal_met"]:
                break
            history = (f"Steps run: {[s.tool for s in steps]}. "
                       f"Critic said missing: {crit.get('missing', '')}")
        res.answer = self._answer(goal, res.steps)
        res.decisions = [d.__dict__ for d in self.manager.decisions]
        res.artifacts = [v["file"] for s in res.steps
                         if isinstance(s.output, dict) and "file" in s.output
                         for v in [s.output]]
        return res
