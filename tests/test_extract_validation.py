"""The extraction model is schema-constrained but never trusted."""
from app.graphrag.extract import Extractor

BAD = {
    "entities": [
        {"type": "Equipment", "name": "Pump 101-A"},
        {"type": "Nonsense", "name": "x"},
        {"type": "Procedure", "name": "SOP-12 Isolation"},
    ],
    "relations": [
        {"source": "Pump 101-A", "target": "SOP-12 Isolation", "type": "GOVERNED_BY"},
        {"source": "ghost", "target": "SOP-12 Isolation", "type": "GOVERNED_BY"},
        {"source": "Pump 101-A", "target": "SOP-12 Isolation", "type": "HACK"},
        {"source": "SOP-12 Isolation", "target": "Pump 101-A", "type": "GOVERNED_BY"},
    ],
}


def test_drops_off_ontology_entities():
    out = Extractor._validate(BAD)
    assert [e["type"] for e in out["entities"]] == ["Equipment", "Procedure"]


def test_keeps_only_the_one_valid_relation():
    out = Extractor._validate(BAD)
    assert len(out["relations"]) == 1
    r = out["relations"][0]
    assert r["type"] == "GOVERNED_BY"
    assert r["source"]["key"] == "Equipment:P-101A"


def test_empty_input_is_safe():
    assert Extractor._validate({}) == {"entities": [], "relations": []}
