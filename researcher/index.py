import pickle
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional

def _load_sentence_transformer():
    # Lazy import to avoid heavy startup cost unless FAISS is used.
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer

def _load_faiss():
    # Lazy import to avoid heavy startup cost unless FAISS is used.
    import faiss
    return faiss

def _load_numpy():
    import numpy as np
    return np


def embed_text(text: str) -> Any:
    """Naive embedding: normalized vector from character ordinals (deterministic, test-friendly)."""
    np = _load_numpy()
    if not text:
        return np.zeros(16, dtype=float)
    vals = [ord(c) % 97 for c in text.lower() if c.isascii()]
    if not vals:
        return np.zeros(16, dtype=float)
    arr = np.array(vals, dtype=float)
    # Pad/trim to 16 dims for stability
    if arr.size < 16:
        arr = np.pad(arr, (0, 16 - arr.size), constant_values=0)
    else:
        arr = arr[:16]
    norm = np.linalg.norm(arr)
    return arr / norm if norm else arr


class SimpleIndex:
    """Lightweight in-memory index for tests/dev; not a production vector store."""

    def __init__(self) -> None:
        self.vectors: List[Any] = []
        self.meta: List[Dict[str, Any]] = []

    def add(self, text: str, meta: Dict[str, Any]) -> None:
        self.vectors.append(embed_text(text))
        self.meta.append(meta)

    def search(self, query: str, k: int = 5) -> List[Tuple[float, Dict[str, Any]]]:
        if not self.vectors:
            return []
        np = _load_numpy()
        qv = embed_text(query)
        scores = []
        for vec, meta in zip(self.vectors, self.meta):
            score = float(np.dot(qv, vec))
            scores.append((score, meta))
        scores.sort(key=lambda x: x[0], reverse=True)
        return scores[:k]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as f:
            pickle.dump({"vectors": self.vectors, "meta": self.meta}, f)

    @classmethod
    def load(cls, path: Path) -> "SimpleIndex":
        idx = cls()
        if not path.exists():
            return idx
        with path.open("rb") as f:
            data = pickle.load(f)
        idx.vectors = data.get("vectors", [])
        idx.meta = data.get("meta", [])
        return idx

    def stats(self) -> Dict[str, Any]:
        return {"count": len(self.meta)}


class FaissIndex:
    """FAISS-backed index with sentence-transformers embeddings."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2", index_path: Path = Path("data/index/faiss.index")) -> None:
        self.model_name = model_name
        self.index_path = index_path
        self.model: Optional[Any] = None
        self.index: Optional[Any] = None
        self.meta: List[Dict[str, Any]] = []

    def _ensure_model(self):
        if self.model is None:
            SentenceTransformer = _load_sentence_transformer()
            self.model = SentenceTransformer(self.model_name)

    def _ensure_index(self, dim: int):
        if self.index is None:
            faiss = _load_faiss()
            self.index = faiss.IndexFlatIP(dim)

    def add(self, texts: List[str], metas: List[Dict[str, Any]]) -> None:
        if not texts:
            return
        np = _load_numpy()
        self._ensure_model()
        embeddings = self.model.encode(texts, normalize_embeddings=True)
        self._ensure_index(embeddings.shape[1])
        self.index.add(np.array(embeddings, dtype="float32"))
        self.meta.extend(metas)

    def search(self, query: str, k: int = 5) -> List[Tuple[float, Dict[str, Any]]]:
        if self.index is None or not self.meta:
            return []
        self._ensure_model()
        q = self.model.encode([query], normalize_embeddings=True)
        scores, idxs = self.index.search(np.array(q, dtype="float32"), k)
        out = []
        for score, idx in zip(scores[0], idxs[0]):
            if idx < 0 or idx >= len(self.meta):
                continue
            out.append((float(score), self.meta[idx]))
        return out

    def save(self) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        if self.index is not None:
            faiss = _load_faiss()
            faiss.write_index(self.index, str(self.index_path))
        meta_path = self.index_path.with_suffix(".meta.pkl")
        with meta_path.open("wb") as f:
            pickle.dump({"meta": self.meta, "model": self.model_name}, f)

    @classmethod
    def load(cls, model_name: str, index_path: Path) -> "FaissIndex":
        obj = cls(model_name=model_name, index_path=index_path)
        meta_path = index_path.with_suffix(".meta.pkl")
        if index_path.exists():
            try:
                faiss = _load_faiss()
                obj.index = faiss.read_index(str(index_path))
            except Exception:
                obj.index = None
        if meta_path.exists():
            with meta_path.open("rb") as f:
                data = pickle.load(f)
            obj.meta = data.get("meta", [])
            obj.model_name = data.get("model", model_name)
        return obj

    def stats(self) -> Dict[str, Any]:
        return {"count": len(self.meta)}
