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
    "local_llm": {
        "enabled": False,
        "streaming": False,
        "fallbacks": [],
    },
    "ingest": {
        "allowlist_roots": [],
        "allowlist_exts": [],
        "allowlist_mode": "warn",
        "scan_proprietary": True,
        "scan_mode": "warn",
        "scan_max_bytes": 200000,
    },
    "trust_policy": {
        "allow_cloud": False,
        "allow_librarian_notes": True,
        "allow_sources": ["internal", "public"],
        "default_source": "internal",
        "cloud_source": "public",
        "encrypt_exports": False,
        "encrypt_when_remote": True,
        "encrypt_logs": False,
        "encrypt_logs_when_remote": True,
        "encryption_key_env": "MARTIN_ENCRYPTION_KEY",
    },
    "remote_transport": {
        "type": "ssh",
        "ssh_user": "",
        "ssh_host": "",
        "local_port": 6001,
        "remote_port": 6001,
        "identity_file": "",
    },
    "embedding_model": "all-MiniLM-L6-v2",  # public HF model
    "vector_store": {"type": "faiss", "index_path": "data/index/faiss.index", "mock_index_path": "data/index/mock_index.pkl", "warm_on_start": False},
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
        "trigger_on_disagreement": True,
        "trigger_on_low_confidence": True,
        "low_confidence_threshold": 0.25,
        "trigger_on_empty_or_decline": True,
        "disagreement_phrases": [
            "no", "nope", "not that", "not what i asked", "wrong",
            "that's wrong", "not correct", "doesn't help", "try again",
            "you're wrong", "that is wrong",
            "think hard", "think harder", "be precise", "be more precise",
            "give me a better answer", "not good enough",
        ],
    },
    "local_only": True,
    "auto_update": {
        "ingest_threshold": 0.1,
        "ingest_cloud_answers": False,
        "sources_on_gap": False,
    },
    "rephraser": {
        "enabled": False,
    },
    "execution": {
        "approval_policy": "on-request",  # on-request|on-failure|never
        "sandbox_mode": "workspace-write",  # read-only|workspace-write|full
        "command_allowlist": [],
        "command_denylist": [],
        "hard_block_outside": False,
        "allowed_roots": [],
        "remote_policy": "block",
    },
    "context": {
        "auto": False,
        "max_recent": 10,
    },
    "ui": {
        "footer": False,
        "api_progress": False,
        "startup_compact": False,
    },
    "socket_server": {
        "host": "127.0.0.1",
        "port": 6001,
        "verbose": False,
    },
    "test_socket": {
        "enabled": False,
        "host": "127.0.0.1",
        "port": 7002,
        "fallback_to_stdin": False,
        "timeout_s": 0,
        "token_env": "MARTIN_TEST_SOCKET_TOKEN",
        "allow_non_loopback": False,
    },
}

_NESTED_KEYS = ("vector_store", "data_paths", "cloud", "execution", "context", "auto_update", "rephraser", "socket_server", "test_socket", "behavior", "logging", "local_llm", "ingest", "trust_policy", "remote_transport", "ui")


def _merge_config(base: Dict[str, Any], loaded: Dict[str, Any]) -> Dict[str, Any]:
    cfg = base.copy()
    for k, v in loaded.items():
        if k in _NESTED_KEYS and isinstance(v, dict):
            merged = cfg.get(k, {}).copy()
            merged.update(v)
            cfg[k] = merged
        else:
            cfg[k] = v
    return cfg

def _normalize_paths(cfg: Dict[str, Any], root: Path) -> Dict[str, Any]:
    data_paths = cfg.get("data_paths", {}) or {}
    for key, val in list(data_paths.items()):
        if not val:
            continue
        p = Path(os.path.expandvars(os.path.expanduser(str(val))))
        if not p.is_absolute():
            p = (root / p).resolve()
        data_paths[key] = str(p)
    cfg["data_paths"] = data_paths

    vs = cfg.get("vector_store", {}) or {}
    for key in ("index_path", "mock_index_path"):
        val = vs.get(key)
        if not val:
            continue
        p = Path(os.path.expandvars(os.path.expanduser(str(val))))
        if not p.is_absolute():
            p = (root / p).resolve()
        vs[key] = str(p)
    cfg["vector_store"] = vs
    return cfg

def load_config(path: Path = Path("config/local.yaml")) -> Dict[str, Any]:
    root = Path(__file__).resolve().parent.parent
    _load_env_file(root / ".env")
    if not path.is_absolute():
        alt = root / path
        if alt.exists():
            path = alt
    if yaml is None:
        cfg = DEFAULT_CONFIG.copy()
        cfg = _normalize_paths(cfg, root)
        ensure_dirs(cfg)
        return cfg
    if not path.exists():
        cfg = DEFAULT_CONFIG.copy()
        cfg = _normalize_paths(cfg, root)
        ensure_dirs(cfg)
        return cfg
    try:
        with path.open("r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        if not isinstance(loaded, dict):
            cfg = DEFAULT_CONFIG.copy()
            ensure_dirs(cfg)
            return cfg
        cfg = _merge_config(DEFAULT_CONFIG, loaded)
        cfg = _normalize_paths(cfg, root)
        ensure_dirs(cfg)
        if cfg.get("local_only"):
            os.environ["RESEARCHER_LOCAL_ONLY"] = "1"
        return cfg
    except Exception:
        cfg = DEFAULT_CONFIG.copy()
        cfg = _normalize_paths(cfg, root)
        ensure_dirs(cfg)
        if cfg.get("local_only"):
            os.environ["RESEARCHER_LOCAL_ONLY"] = "1"
        return cfg


def ensure_dirs(cfg: Dict[str, Any]) -> None:
    root = Path(__file__).resolve().parent.parent
    def _resolve(p: str) -> Path:
        expanded = Path(os.path.expandvars(os.path.expanduser(p)))
        if not expanded.is_absolute():
            return (root / expanded).resolve()
        return expanded

    data_paths = cfg.get("data_paths", {})
    for key in ("raw", "processed", "index", "logs"):
        p = data_paths.get(key)
        if not p:
            continue
        _resolve(p).mkdir(parents=True, exist_ok=True)

    vs = cfg.get("vector_store", {}) or {}
    for key in ("index_path", "mock_index_path"):
        p = vs.get(key)
        if not p:
            continue
        _resolve(p).parent.mkdir(parents=True, exist_ok=True)


def _load_env_file(path: Path = Path(".env")) -> None:
    """Best-effort .env loader (no external deps)."""
    if not path.exists():
        return
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip("'").strip('"')
            if key and key not in os.environ:
                os.environ[key] = val
    except Exception:
        pass


def env_key_set() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())
