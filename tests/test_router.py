from app.router.classifier import route
from app.graphrag.retrieve import MODE_GLOBAL, MODE_LOCAL, MODE_TRAVERSE, MODE_VECTOR, plan


def test_image_forces_vision():
    assert route("what is this", has_image=True).task == "vision"


def test_code_and_doc_signals():
    assert route("write a python function for NPSH").task == "code"
    assert route("draft an approval note").task == "doc"


def test_retrieval_modes():
    assert plan("If we isolate P-101A what is affected?").mode == MODE_TRAVERSE
    assert plan("recurring failure themes across reports").mode == MODE_GLOBAL
    assert plan("what does the SOP say about P-101A").mode == MODE_LOCAL
    assert plan("what is the design pressure").mode == MODE_VECTOR


def test_traverse_extracts_anchor():
    assert plan("impact if P-101A trips").anchors == ["P-101A"]
