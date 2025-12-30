from pathlib import Path

from researcher.test_helpers import suggest_test_commands


def test_suggest_test_commands_basic(tmp_path: Path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    cmds = suggest_test_commands(tmp_path)
    assert "python -m pytest tests" in cmds
    assert "python -m pytest -q" in cmds


def test_suggest_test_commands_ingest_demo(tmp_path: Path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "ingest_demo.py").write_text("# demo\n", encoding="utf-8")
    cmds = suggest_test_commands(tmp_path)
    assert "python scripts/ingest_demo.py --simple-index" in cmds
