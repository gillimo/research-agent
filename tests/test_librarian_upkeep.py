import json

import researcher.librarian as lib


def test_read_recent_gap_events(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.ndjson"
    # Write two gap events and one non-gap
    lines = [
        {"entry": {"ts": "2025-01-01T00:00:00Z", "event": "other", "data": {}}},
        {"entry": {"ts": "2025-01-01T00:00:01Z", "event": "rag_gap", "data": {"prompt": "alpha", "top_score": 0.01}}},
        {"entry": {"ts": "2025-01-01T00:00:02Z", "event": "rag_gap", "data": {"prompt": "beta", "top_score": 0.02}}},
    ]
    ledger.write_text("\n".join(json.dumps(l) for l in lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(lib, "LEDGER_FILE", ledger)
    gaps = lib._read_recent_gap_events("", limit=10)
    assert len(gaps) == 2
    gaps2 = lib._read_recent_gap_events("2025-01-01T00:00:01Z", limit=10)
    assert len(gaps2) == 1
    assert gaps2[0]["data"]["prompt"] == "beta"
