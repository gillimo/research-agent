from pathlib import Path

from researcher.context_harvest import gather_context


def test_context_pack_fields(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    ctx = gather_context(tmp_path, max_recent=5)
    assert "tech_stack" in ctx
    assert "python" in ctx["tech_stack"]
    assert "tree" in ctx
    assert any("src" in p for p in ctx["tree"])
    assert "recent_files" in ctx
