import json
from pathlib import Path

from researcher.cloud_bridge import _hash


def test_cloud_log_fixture_hash_matches():
    fixture = Path("tests/fixtures/cloud_log_sample.ndjson")
    line = fixture.read_text(encoding="utf-8").strip()
    data = json.loads(line)
    entry = data.get("entry", {})
    payload = entry.get("data", {})
    sanitized = payload.get("sanitized", "")
    assert _hash(sanitized) == payload.get("hash")
