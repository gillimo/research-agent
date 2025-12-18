from pathlib import Path

from researcher.index import SimpleIndex


def test_simple_index_add_search(tmp_path: Path):
    idx = SimpleIndex()
    idx.add("hello world", {"path": "doc1"})
    idx.add("another thing", {"path": "doc2"})
    hits = idx.search("hello", k=1)
    assert len(hits) == 1
    score, meta = hits[0]
    assert meta["path"] == "doc1"
    assert score > 0


def test_simple_index_save_load(tmp_path: Path):
    idx = SimpleIndex()
    idx.add("content", {"path": "doc"})
    out = tmp_path / "idx.pkl"
    idx.save(out)
    loaded = SimpleIndex.load(out)
    hits = loaded.search("content", k=1)
    assert hits and hits[0][1]["path"] == "doc"
