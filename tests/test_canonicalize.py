"""Tag resolution is the highest-risk deterministic component: a missed merge
silently fragments the graph and every traversal returns a partial answer."""
import pytest
from app.graphrag.canonicalize import canonical_key, normalize_tag

P101A = ["P-101A", "P101A", "Pump 101-A", "pump P101 A", "PUMP-101A",
         "the main pump P-101 A", "centrifugal pump 101 A", "Pump no. 101 A"]


@pytest.mark.parametrize("raw", P101A)
def test_all_spellings_merge(raw):
    assert normalize_tag(raw) == "P-101A"


def test_single_node_for_all_spellings():
    assert len({canonical_key("Equipment", r) for r in P101A}) == 1


@pytest.mark.parametrize("raw,want", [
    ("TI-2043", "TI-2043"), ("V-1201", "V-1201"),
    ("Exchanger 204", "E-204"), ("heat exchanger E204", "E-204"),
])
def test_other_tags(raw, want):
    assert normalize_tag(raw) == want


@pytest.mark.parametrize("raw", ["Fire Water", "Chief Engineer", "", "crude oil"])
def test_non_tags_rejected(raw):
    assert normalize_tag(raw) is None


def test_suffix_letter_never_dropped():
    """Regression: a stopword list containing bare 'A' ate the suffix,
    splitting P-101A from P-101."""
    assert normalize_tag("P-101 A") != normalize_tag("P-101")
