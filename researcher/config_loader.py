import os
from pathlib import Path
from typing import Any, Dict

try:
    import yaml
except Exception:
    yaml = None


DEFAULT_CONFIG: Dict[str, Any] = {
    "local_model": "phi3",
    "ollama_host": "http://localhost:11434",
    "local_llm_enabled": False,
    "embedding_model": "all-MiniLM-L6-v2",  # public HF model
    "vector_store": {"type": "faiss", "index_path": "data/index/faiss.index", "mock_index_path": "data/index/mock_index.pkl"},
    "data_paths": {
        "raw": "data/raw",
        "processed": "data/processed",
        "index": "data/index",
        "logs": "logs",
    },
    "cloud": {
        "enabled": False,
        "provider": "",
        "model": "",
        "cmd_template": "",
        "trigger_score": 0.3,
    },
}


def load_config(path: Path = Path("config/local.yaml")) -> Dict[str, Any]:
    if yaml is None:
        return DEFAULT_CONFIG.copy()
    if not path.exists():
        return DEFAULT_CONFIG.copy()
    try:
        with path.open("r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        if not isinstance(loaded, dict):
            return DEFAULT_CONFIG.copy()
        cfg = DEFAULT_CONFIG.copy()
        cfg.update(loaded)
        return cfg
    except Exception:
        return DEFAULT_CONFIG.copy()


def ensure_dirs(cfg: Dict[str, Any]) -> None:
    data_paths = cfg.get("data_paths", {})
    for key in ("raw", "processed", "index", "logs"):
        p = data_paths.get(key)
        if not p:
            continue
        Path(p).mkdir(parents=True, exist_ok=True)


def env_key_set() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())
