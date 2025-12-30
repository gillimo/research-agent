import os

from researcher.cloud_bridge import call_cloud


def test_local_only_blocks_cloud(monkeypatch):
    monkeypatch.setenv("RESEARCHER_LOCAL_ONLY", "1")
    res = call_cloud("test prompt")
    assert res.ok is False
    assert "local-only" in res.error or "local only" in res.error
