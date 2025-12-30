import researcher.librarian as lib


def test_parse_sources():
    text = "- Site A https://a.example.com\n2. Site B https://b.example.com\nPlain line"
    sources = lib._parse_sources(text)
    assert sources[0].startswith("Site A")
    assert sources[1].startswith("Site B")
    assert "Plain line" in sources
