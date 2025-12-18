from researcher.provenance import build_response


def test_build_response_merges_cloud_provenance():
    hits = [(0.8, {"path": "local_doc", "chunk": "local chunk"})]
    cloud_hits = [(0.0, {"path": "cloud", "chunk": "cloud chunk"})]
    resp = build_response("id", "answer", hits, logs_ref="logs/local.log", cloud_hits=cloud_hits)
    assert resp.provenance["local"][0].source == "local_doc"
    assert resp.provenance["cloud"][0].source == "cloud"
    assert resp.confidence.local == 0.8
    assert resp.confidence.cloud == 0.0
