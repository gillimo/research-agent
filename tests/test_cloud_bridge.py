import sys

from researcher.cloud_bridge import call_cloud


def test_call_cloud_no_config_returns_error(tmp_path, monkeypatch):
    monkeypatch.delenv("RESEARCHER_CLOUD_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("RESEARCHER_CLOUD_PROVIDER", raising=False)
    monkeypatch.delenv("RESEARCHER_LOCAL_ONLY", raising=False)

    result = call_cloud("hello", cmd_template=None, logs_root=tmp_path)

    assert result.ok is False
    assert "No cloud API key/provider" in result.error


def test_call_cloud_cmd_template_executes(tmp_path, monkeypatch):
    monkeypatch.delenv("RESEARCHER_CLOUD_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("RESEARCHER_CLOUD_PROVIDER", raising=False)
    monkeypatch.setenv("RESEARCHER_LOCAL_ONLY", "0")

    cmd_template = f"\"{sys.executable}\" -c \"print('ok')\""
    result = call_cloud("hello", cmd_template=cmd_template, logs_root=tmp_path)

    assert result.ok is True
    assert result.output == "ok"


def test_call_cloud_cmd_template_rejects_unsafe(tmp_path, monkeypatch):
    monkeypatch.delenv("RESEARCHER_CLOUD_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("RESEARCHER_CLOUD_PROVIDER", raising=False)
    monkeypatch.setenv("RESEARCHER_LOCAL_ONLY", "0")

    cmd_template = f"\"{sys.executable}\" -c \"print('ok')\" | more"
    result = call_cloud("hello", cmd_template=cmd_template, logs_root=tmp_path)

    assert result.ok is False
    assert "cmd_template" in result.error
