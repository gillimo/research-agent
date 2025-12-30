from researcher import chat_ui


def test_palette_entries_basic():
    entries = chat_ui.build_palette_entries("pal", ["/palette", "/help"], ["You: palette test"])
    kinds = [k for k, _ in entries]
    assert "cmd" in kinds


def test_file_entries_basic(tmp_path):
    (tmp_path / "alpha.txt").write_text("a", encoding="utf-8")
    (tmp_path / "beta.md").write_text("b", encoding="utf-8")
    entries = chat_ui.build_file_entries("alpha", max_items=10, max_depth=2, root=tmp_path)
    # Ensure at least one path contains alpha
    assert any("alpha" in e for e in entries)


def test_palette_entries_search_sources(tmp_path):
    (tmp_path / "alpha.txt").write_text("a", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    out_dir = tmp_path / "logs" / "outputs"
    out_dir.mkdir(parents=True)
    (out_dir / "run.log").write_text("ok", encoding="utf-8")

    entries = chat_ui.build_palette_entries("alpha", ["/help"], [], root=tmp_path)
    assert any(k == "file" for k, _ in entries)

    entries = chat_ui.build_palette_entries("pytest", ["/help"], [], root=tmp_path)
    assert any(k == "test" for k, _ in entries)

    entries = chat_ui.build_palette_entries("run", ["/help"], [], root=tmp_path)
    assert any(k == "output" for k, _ in entries)
