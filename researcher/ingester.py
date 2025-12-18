import re
from pathlib import Path
from typing import Iterable, List, Dict, Any

from researcher.index import SimpleIndex, FaissIndex


def simple_chunk(text: str, max_chars: int = 800, overlap: int = 80) -> List[str]:
    cleaned = re.sub(r"\s+", " ", text).strip()
    chunks: List[str] = []
    start = 0
    n = len(cleaned)
    while start < n:
        end = min(n, start + max_chars)
        chunks.append(cleaned[start:end])
        if end == n:
            break
        start = end - overlap
    return [c for c in chunks if c]


def ingest_files(idx, files: Iterable[Path]) -> Dict[str, Any]:
    ingested = 0
    errors: List[str] = []
    for fp in files:
        try:
            text = fp.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            errors.append(f"{fp}: {e}")
            continue
        chunks = simple_chunk(text)
        if isinstance(idx, FaissIndex):
            idx.add(chunks, [{"path": str(fp), "chunk": c[:200]} for c in chunks])
        else:
            for chunk in chunks:
                idx.add(chunk, {"path": str(fp), "chunk": chunk[:200]})
        ingested += 1
    return {"ingested": ingested, "errors": errors}
