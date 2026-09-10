"""Re-running ingestion is a constant during development; it must be idempotent."""
import pytest
from app.graphrag.store import GraphStore


@pytest.fixture
def store(tmp_path):
    s = GraphStore(tmp_path / "g")
    s.upsert_node("Equipment", "Equipment:P-101A", "P-101A", {"unit": "CDU"})
    s.upsert_node("Equipment", "Equipment:E-204", "E-204", {"unit": "CDU"})
    s.upsert_node("Equipment", "Equipment:V-1201", "V-1201", {"unit": "CDU"})
    s.upsert_node("Vendor", "Vendor:kbl", "Kirloskar", {"name": "Kirloskar"})
    return s


def test_vendor_name_attr_does_not_collide(store):
    """Person/Vendor/Material declare 'name', which duplicates the built-in column."""
    assert store.stats()["Vendor"] == 1


def test_relations_are_idempotent(store):
    assert store.add_rel("FEEDS", "Equipment", "Equipment:P-101A",
                         "Equipment", "Equipment:E-204") is True
    assert store.add_rel("FEEDS", "Equipment", "Equipment:P-101A",
                         "Equipment", "Equipment:E-204") is False
    rows = store.rows("MATCH (a:Equipment)-[r:FEEDS]->(b:Equipment) RETURN count(r) AS c")
    assert rows[0]["c"] == 1


def test_upsert_is_idempotent(store):
    store.upsert_node("Equipment", "Equipment:P-101A", "P-101A", {"unit": "CDU-2"})
    assert store.stats()["Equipment"] == 3


def test_multi_hop_reaches_indirect_dependency(store):
    """The GraphRAG claim: V-1201 is 2 hops from P-101A and has no textual
    similarity to it. Vector search cannot find this; traversal can."""
    store.add_rel("FEEDS", "Equipment", "Equipment:P-101A", "Equipment", "Equipment:E-204")
    store.add_rel("FEEDS", "Equipment", "Equipment:E-204", "Equipment", "Equipment:V-1201")
    names = {n["name"] for n in store.neighborhood("Equipment", "Equipment:P-101A", hops=2)}
    assert "V-1201" in names
    one_hop = {n["name"] for n in store.neighborhood("Equipment", "Equipment:P-101A", hops=1)}
    assert "V-1201" not in one_hop


def test_vector_ids_are_deterministic():
    """Re-running ingestion must upsert, not append a second copy of the corpus."""
    from app.retrieval.vector import point_id
    assert point_id("doc-p1-0-abc") == point_id("doc-p1-0-abc")
    assert point_id("doc-p1-0-abc") != point_id("doc-p1-1-abc")
