from typing import List, Tuple, Dict, Any


def compose_answer(hits: List[Tuple[float, Dict[str, Any]]], max_chunks: int = 3) -> str:
    """Combine top chunks into a simple answer string."""
    parts = []
    for score, meta in hits[:max_chunks]:
        chunk = meta.get("chunk") or meta.get("text") or ""
        src = meta.get("path", "unknown")
        parts.append(f"[{score:.2f} {src}] {chunk}")
    return "\n".join(parts) if parts else "(no results)"
