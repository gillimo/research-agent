from researcher.cloud_bridge import call_cloud


def test_call_cloud_no_config_returns_error(tmp_path, monkeypatch):
    monkeypatch.delenv("RESEARCHER_CLOUD_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("RESEARCHER_CLOUD_PROVIDER", raising=False)

    result = call_cloud("hello", cmd_template=None, logs_root=tmp_path)

    assert result.ok is False
    assert "No cloud API key/provider" in result.error
