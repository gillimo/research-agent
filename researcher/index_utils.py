from pathlib import Path
import os
import sys
from typing import Dict, Any

from researcher.config_loader import load_config
from researcher.index import SimpleIndex, FaissIndex

def load_index_from_config(cfg: Dict[str, Any]):
    """
    Loads the appropriate vector store (FAISS or SimpleIndex) based on configuration.
    This function is extracted from researcher/cli.py to be shared by other modules
    like the Librarian.
    """
    vs = cfg.get("vector_store", {}) or {}
    index_path = Path(vs.get("index_path", "data/index/mock_index.pkl"))
    mock_path = Path(vs.get("mock_index_path", "data/index/mock_index.pkl"))
    idx_type = vs.get("type", "simple")
    if os.environ.get("RESEARCHER_FORCE_SIMPLE_INDEX", "").strip().lower() in {"1", "true", "yes"}:
        return SimpleIndex.load(mock_path)
    if idx_type == "faiss":
        model = cfg.get("embedding_model", "all-MiniLM-L6-v2")
        try:
            idx = FaissIndex.load(model_name=model, index_path=index_path)
            # probe model availability early
            idx._ensure_model()
            return idx
        except Exception as e:
            print(f"[warn] FAISS/embedding load failed ({e}); falling back to SimpleIndex {mock_path}", file=sys.stderr)
            return SimpleIndex.load(mock_path)
    return SimpleIndex.load(mock_path)

if __name__ == "__main__":
    # Example usage for testing
    print("--- Testing index_utils ---")
    cfg = load_config()
    idx = load_index_from_config(cfg)
    print(f"Loaded index type: {type(idx).__name__}")
    print(f"Index stats: {idx.stats()}")
    print("--- End Testing ---")
