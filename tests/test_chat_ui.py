from researcher import chat_ui


def test_palette_entries_basic():
    entries = chat_ui.build_palette_entries("pal", ["/palette", "/help"], ["You: palette test"])
    kinds = [k for k, _ in entries]
    assert "cmd" in kinds
