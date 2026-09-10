"""Entity resolution for industrial tags.

This is where naive GraphRAG fails. 'P-101A', 'P101A', 'Pump 101-A' and
'pump P101 A' must collapse to one node or every traversal returns a fragment
of the truth. Industrial tags are highly regular, so rules beat embeddings
here - and rules are deterministic, which matters when a judge re-runs a query.
"""
from __future__ import annotations

import re
import unicodedata

# ISA-5.1 shaped tag found anywhere in a string:
#   function letters - loop digits - optional single suffix letter
_TAG = re.compile(
    r"(?<![A-Z0-9])([A-Z]{1,6})[\s\-_]?(\d{2,5})(?:[\s\-_]?([A-Z])(?![A-Z0-9]))?"
)

# Spelled-out equipment nouns mapped to their conventional function code, so
# "Pump 101-A" and "P-101A" resolve to the same node.
_EQUIP_CODE = {
    "PUMP": "P", "VALVE": "V", "VESSEL": "V", "COLUMN": "C", "TOWER": "C",
    "EXCHANGER": "E", "COOLER": "E", "COMPRESSOR": "K", "TANK": "TK",
    "DRUM": "D", "HEATER": "H", "FURNACE": "H", "REACTOR": "R",
    "FILTER": "F", "BLOWER": "B", "FAN": "B", "TURBINE": "T", "MOTOR": "M",
}

# NOTE: never strip a bare "A" here - it is a valid tag suffix (P-101A).
_STOP = re.compile(r"\b(THE|MAIN|NO\.?|NUMBER|UNIT)\b")

# Substituted BEFORE the tag regex so spelled-out nouns of any length work.
_EQUIP_RE = re.compile(r"\b(" + "|".join(_EQUIP_CODE) + r")\b")
def _tagged() -> set[str]:
    """Which types carry a structured identifier - read from the active
    ontology, so a non-industrial corpus is not forced through tag parsing."""
    from app.graphrag.schema import tagged_types
    return tagged_types()


def normalize_tag(raw: str) -> str | None:
    """Return canonical 'P-101A' form, or None if this is not a tag."""
    s = unicodedata.normalize("NFKC", raw).upper().strip()
    s = _STOP.sub(" ", s)
    s = _EQUIP_RE.sub(lambda m: _EQUIP_CODE[m.group(1)], s)
    s = re.sub(r"[.,;:()]", " ", s)   # "Pump No. 101 A" -> "P 101 A"
    s = re.sub(r"\s+", " ", s).strip()

    for m in _TAG.finditer(s):
        fn, loop, suf = m.group(1), m.group(2), m.group(3) or ""
        if len(fn) > 4:            # not a plausible function code
            continue
        return f"{fn}-{loop}{suf}"
    return None


def canonical_key(ntype: str, name: str) -> str:
    """Stable primary key. Tagged assets key on their tag so every mention
    across every document lands on the same node."""
    if ntype in _tagged():
        tag = normalize_tag(name)
        if tag:
            return f"{ntype}:{tag}"
    slug = re.sub(r"\s+", "_", _STOP.sub(" ", name.upper()).strip().lower())
    slug = re.sub(r"[^\w_\-.]", "", slug).strip("_")[:80]
    return f"{ntype}:{slug}"


def display_name(ntype: str, name: str) -> str:
    if ntype in _tagged():
        return normalize_tag(name) or name.strip()
    return name.strip()
