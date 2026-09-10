"""Symbolic calculation that SHOWS ITS WORK.

The PS asks for "calculations with steps shown". An LLM doing arithmetic in
prose is both unreliable and unauditable; SymPy is exact and the substitution
trail is a real derivation an engineer can check.
"""
from __future__ import annotations

import sympy as sp


def evaluate(expression: str, variables: dict[str, float] | None = None,
             solve_for: str | None = None) -> dict:
    steps: list[str] = []
    local = {k: sp.Symbol(k) for k in (variables or {})}

    # Planners habitually write "pressure_drop = flow * resistance". Treat a
    # single '=' as a definition and evaluate the right-hand side.
    raw = expression.strip()

    # Planners often emit several equations, one per line. Use the first line
    # that actually parses rather than failing the whole call.
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    if len(lines) > 1:
        for line in lines:
            probe = line.split("=", 1)[1] if ("=" in line and "==" not in line) else line
            try:
                sp.sympify(probe, locals=local)
                raw = line
                steps.append(f"Multiple expressions supplied; evaluating: {line}")
                break
            except Exception:
                continue
        else:
            raise ValueError(
                "None of the supplied lines is a valid expression. "
                "Send ONE expression, e.g. '(Ps-Pv)/(rho*g)+Hs-Hf'.")

    if "=" in raw and "==" not in raw:
        lhs, rhs = raw.split("=", 1)
        if lhs.strip().replace("_", "").isalnum():
            steps.append(f"Definition: {lhs.strip()} = {rhs.strip()}")
            raw = rhs.strip()
            solve_for = solve_for or None
        else:
            raw = f"({lhs.strip()}) - ({rhs.strip()})"
            steps.append(f"Rearranged to: {raw} = 0")

    expr = sp.sympify(raw, locals=local)
    steps.append(f"Expression: {sp.pretty(expr)}")

    free = {str(x) for x in expr.free_symbols}
    unknown = free - set(variables or {})
    if unknown and not solve_for:
        steps.append(f"Symbolic result (no values given for: {sorted(unknown)})")

    if solve_for:
        sym = sp.Symbol(solve_for)
        sol = sp.solve(sp.Eq(expr, 0), sym) if not isinstance(expr, sp.Eq) \
            else sp.solve(expr, sym)
        steps.append(f"Solve for {solve_for}: {sol}")
        return {"steps": steps, "result": [str(s) for s in sol], "exact": True}

    simplified = sp.simplify(expr)
    if simplified != expr:
        steps.append(f"Simplified: {sp.pretty(simplified)}")

    if variables:
        subs = {sp.Symbol(k): sp.Float(v) for k, v in variables.items()}
        steps.append("Substitute: " + ", ".join(f"{k} = {v}" for k, v in variables.items()))
        substituted = simplified.subs(subs)
        steps.append(f"After substitution: {sp.pretty(substituted)}")
        value = sp.N(substituted, 8)
        steps.append(f"Result: {value}")
        return {"steps": steps, "result": str(value), "exact": False}

    return {"steps": steps, "result": str(simplified), "exact": True}


NPSH_EXAMPLE = {
    "expression": "(Ps - Pv)/(rho*g) + Hs - Hf",
    "variables": {"Ps": 101325, "Pv": 2339, "rho": 998, "g": 9.81, "Hs": 2.5, "Hf": 0.8},
}
