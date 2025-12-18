from pathlib import Path
from researcher.index import SimpleIndex
from researcher.ingester import ingest_files


def test_ingest_files(tmp_path: Path):
    f = tmp_path / "doc.txt"
    f.write_text("hello world " * 50, encoding="utf-8")
    idx = SimpleIndex()
    result = ingest_files(idx, [f])
    assert result["ingested"] == 1
    hits = idx.search("hello", k=1)
    assert hits and hits[0][1]["path"] == str(f)
