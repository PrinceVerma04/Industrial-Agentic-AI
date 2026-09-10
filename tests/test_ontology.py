"""The ontology is config, not code: a new domain must be a YAML pack."""
import pytest

from app.graphrag.schema import RESERVED, build


def test_general_pack_is_domain_neutral():
    o = build(("general",))
    assert "Person" in o["nodes"] and "Concept" in o["nodes"]
    assert "Equipment" not in o["nodes"], "general pack must not carry refinery types"
    assert o["tagged"] == set(), "nothing in a general corpus is tag-shaped"


def test_industrial_pack_adds_types_and_tagging():
    o = build(("general", "industrial"))
    assert {"Equipment", "Instrument", "Line"} <= set(o["nodes"])
    assert o["tagged"] == {"Equipment", "Instrument", "Line"}


def test_relation_names_merge_across_packs():
    """PART_OF exists in both packs with different endpoints; the second
    definition must not silently replace the first."""
    pairs = build(("general", "industrial"))["relations"]["PART_OF"]
    assert ("Section", "Document") in pairs
    assert ("Equipment", "Equipment") in pairs


def test_cross_pack_endpoints_dropped_when_type_absent():
    """industrial's REQUIRES_APPROVAL_FROM targets Person, which lives in
    general. Enabled alone it must degrade, not crash."""
    o = build(("industrial",))
    assert "Person" not in o["nodes"]
    assert "REQUIRES_APPROVAL_FROM" not in o["relations"]
    assert "MENTIONED_IN" not in o["relations"]      # targets Document
    assert "FEEDS" in o["relations"]                 # wholly self-contained


def test_reserved_attributes_never_collide():
    for pack in ("general", "industrial"):
        for node, attrs in build((pack,))["nodes"].items():
            assert not (set(attrs) & RESERVED), f"{node} redeclares a built-in column"


def test_unknown_pack_is_rejected():
    with pytest.raises(ValueError, match="unknown ontology pack"):
        build(("does-not-exist",))


def test_every_relation_endpoint_is_a_known_node():
    o = build(("general", "industrial"))
    for rel, pairs in o["relations"].items():
        for s, d in pairs:
            assert s in o["nodes"] and d in o["nodes"], f"{rel} references unknown type"
