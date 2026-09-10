"""Regression tests for the four bugs found during the first end-to-end runs."""
import pytest

from app.agent.loop import Agent, _FAILED
from app.tools.calc import evaluate
from app.tools.registry import coerce_args
from app.tools.render import render

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "findings": {"type": "array", "items": {"type": "string"}},
        "citations": {"type": "array", "items": {"type": "object"}},
        "rows": {"type": "array", "items": {"type": "array"}},
        "hops": {"type": "integer"},
    },
    "required": ["title", "findings"],
}


class TestCoercion:
    """Planners emit a string where an array is declared; failing the call
    over that burns a whole agent iteration."""

    def test_string_becomes_array(self):
        out, _ = coerce_args(SCHEMA, {"title": "x", "findings": "- a\n- b"})
        assert out["findings"] == ["a", "b"]

    def test_strings_become_objects(self):
        out, _ = coerce_args(SCHEMA, {"title": "x", "findings": [], "citations": ["SOP-12"]})
        assert out["citations"] == [{"text": "SOP-12"}]

    def test_numeric_string_becomes_int(self):
        out, _ = coerce_args(SCHEMA, {"title": "x", "findings": [], "hops": "3"})
        assert out["hops"] == 3

    def test_unknown_keys_dropped_and_missing_reported(self):
        out, notes = coerce_args(SCHEMA, {"junk": 1})
        assert "junk" not in out
        assert any(n.startswith("MISSING REQUIRED") for n in notes)


class TestStepDataFlow:
    """The planner writes all arguments before anything runs, so it must be
    able to reference earlier results instead of inventing placeholders."""

    def test_placeholder_is_substituted(self):
        got = Agent._resolve({"body": "Affected: {{step1}}"},
                             {1: {"found": True,
                                  "anchor": {"key": "Equipment:P-101A", "name": "P-101A"},
                                  "neighbourhood": [{"type": "Equipment",
                                                     "key": "Equipment:E-204",
                                                     "name": "E-204"}]}})
        assert "E-204" in got["body"]
        assert "{" not in got["body"], "raw dict repr leaked into prose"

    def test_failed_step_never_leaks_an_error_into_a_deliverable(self):
        got = Agent._resolve({"body": "Result: {{step1}}"}, {1: _FAILED})
        assert "upstream step failed" in got["body"]
        assert "Error" not in got["body"] and "Traceback" not in got["body"]

    def test_unrun_step_is_marked(self):
        assert "[step not run]" in Agent._resolve({"b": "{{step9}}"}, {})["b"]

    def test_nested_structures_resolved(self):
        got = Agent._resolve({"findings": ["a", "{{step1}}"]}, {1: "real value"})
        assert got["findings"] == ["a", "real value"]


class TestRender:
    def test_graph_output_is_prose(self):
        txt = render({"found": True,
                      "anchor": {"key": "Equipment:P-101A", "name": "P-101A"},
                      "neighbourhood": [{"type": "Equipment", "key": "Equipment:E-204",
                                         "name": "E-204"}]})
        assert "E-204 (Equipment)" in txt and "{" not in txt

    def test_calc_output_shows_steps(self):
        assert "Derivation" in render({"steps": ["a"], "result": "42"})

    def test_missing_entity_is_explained(self):
        assert "No entity" in render({"found": False, "entity": "X-999",
                                      "neighbourhood": []})


class TestCalc:
    def test_assignment_form_accepted(self):
        assert evaluate("pressure_drop = P1 - P2")["result"] == "P1 - P2"

    def test_multiline_uses_first_parseable(self):
        assert evaluate("delta_P = P1 - P2\ndelta_Q = Q1 - Q2")["result"] == "P1 - P2"

    def test_numeric_substitution_exact(self):
        r = evaluate("(Ps-Pv)/(rho*g)+Hs-Hf",
                     {"Ps": 101325, "Pv": 2339, "rho": 998, "g": 9.81, "Hs": 2.5, "Hf": 0.8})
        assert abs(float(r["result"]) - 11.810537) < 1e-5
        assert any("Substitute" in s for s in r["steps"])

    def test_unparseable_raises_clearly(self):
        with pytest.raises(Exception):
            evaluate("!!! not maths @@@\n### also not")


class TestToolFailureDetection:
    """run_python reports failure by RETURNING {"ok": False}, not by raising.
    Regression: a SyntaxError inside the sandbox was marked [ok] and its
    stderr was written into a generated spreadsheet as data."""

    @staticmethod
    def _ok(result):
        return not (isinstance(result, dict) and result.get("ok") is False)

    def test_value_returned_failure_is_detected(self):
        assert self._ok({"ok": False, "stderr": "SyntaxError"}) is False

    def test_success_and_ok_less_results_pass(self):
        assert self._ok({"ok": True, "stdout": "42"}) is True
        assert self._ok({"file": "/tmp/x.docx"}) is True
        assert self._ok("plain string") is True

    def test_sandbox_syntax_error_reports_not_ok(self):
        from app.tools.sandbox import run_python
        r = run_python("def broken(\n  results")
        assert r.ok is False
        assert self._ok({"ok": r.ok, "stderr": r.stderr}) is False


class TestPlaceholderForms:
    """Regression: an unsubstituted placeholder reached a generated .xlsx, and
    prose substituted into Python source caused SyntaxError every time."""

    RES = {2: {"headers": ["Name", "Skills"], "rows": [["Prince", "Python"]]}}

    def _r(self, t, res=None):
        return Agent._resolve({"x": t}, res if res is not None else self.RES)["x"]

    def test_subscripted_reference_resolves(self):
        assert "Name" in self._r("{{step2['headers']}}")

    def test_json_filter_yields_a_valid_python_literal(self):
        import ast
        out = self._r("{{step2|json}}")
        assert ast.literal_eval(out) == self.RES[2]

    def test_json_filter_makes_generated_code_compile(self):
        src = self._r("data = {{step2|json}}\nprint(len(data['rows']))")
        compile(src, "<test>", "exec")          # would raise on prose substitution

    def test_missing_key_is_explicit_not_silent(self):
        assert "no 'missing'" in self._r("{{step2['missing']}}")

    def test_unknown_placeholder_never_survives_verbatim(self):
        out = self._r("{{ bogus }}")
        assert "{{" not in out and "unresolved" in out

    def test_failed_step_marker_still_applies(self):
        assert "upstream step failed" in self._r("{{step3}}", {3: _FAILED})
