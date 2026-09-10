"""Human-readable rendering of tool output.

Raw tool results are dicts. Substituting a dict's repr into a document body
produces text like "Isolation would cause {'found': True, 'anchor': {...}}".
Anything that flows into prose - a {{stepN}} substitution, or the synthesis
prompt - goes through here first.
"""
from __future__ import annotations

import json
from typing import Any

MAX = 3000


def render(output: Any) -> str:
    if output is None:
        return ""
    if isinstance(output, str):
        return output[:MAX]
    if isinstance(output, list):
        return "\n".join(f"- {render(o)}" for o in output)[:MAX]
    if not isinstance(output, dict):
        return str(output)[:MAX]

    # graph_query
    if "neighbourhood" in output:
        if not output.get("found"):
            return f"No entity matching '{output.get('entity', '?')}' in the graph."
        anchor = output.get("anchor", {})
        rows = output["neighbourhood"]
        body = "\n".join(f"- {n['name']} ({n['type']})" for n in rows
                         if n.get("key") != anchor.get("key"))
        return (f"{anchor.get('name', '?')} is structurally connected to "
                f"{len([r for r in rows if r.get('key') != anchor.get('key')])} "
                f"entities:\n{body}")[:MAX]

    # kb_search
    if "context" in output:
        return f"[retrieval mode: {output.get('mode', '?')}]\n{output['context']}"[:MAX]

    # calculate
    if "steps" in output and "result" in output:
        return ("Derivation:\n" + "\n".join(f"  {s}" for s in output["steps"])
                + f"\nResult: {output['result']}")[:MAX]

    # docgen
    if "file" in output:
        return f"Generated file: {output['file']}"

    # sandbox
    if "stdout" in output:
        head = "Execution succeeded." if output.get("ok") else "Execution FAILED."
        out = (output.get("stdout") or "").strip()
        err = (output.get("stderr") or "").strip()
        return f"{head}\nstdout:\n{out}" + (f"\nstderr:\n{err}" if err else "")[:MAX]

    return json.dumps(output, indent=2, default=str)[:MAX]
