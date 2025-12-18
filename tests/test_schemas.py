from researcher.schemas import Request, Response, IngestResult, ProvenanceEntry


def test_request_defaults_and_validation():
    req = Request(id="1", query="hello world")
    assert req.settings.k == 5
    assert req.mode == "ask"
    assert req.context.tags == []


def test_response_provenance_structure():
    prov = {"local": [ProvenanceEntry(source="a", score=0.9, text="t1")]}
    resp = Response(id="1", answer="ok", provenance=prov)
    assert resp.provenance["local"][0].source == "a"


def test_ingest_result_defaults():
    ing = IngestResult(id="1")
    assert ing.ingested == []
    assert ing.errors == []
