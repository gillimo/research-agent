import os
from pathlib import Path
from typing import Dict, Any, List, Tuple

from researcher.state_manager import ROOT_DIR

DEFAULT_MAX_ITEMS = 200
DEFAULT_MAX_DEPTH = 4
DEFAULT_MAX_BYTES = 64 * 1024

_SKIP_DIRS = {".git", "__pycache__", "logs", ".pytest_cache"}


def _is_within_root(path: Path, root: Path) -> bool:
    try:
        path = path.resolve()
        root = root.resolve()
    except Exception:
        return False
    return str(path).startswith(str(root) + os.sep) or str(path) == str(root)


def list_resources(root: Path = ROOT_DIR, max_items: int = DEFAULT_MAX_ITEMS, max_depth: int = DEFAULT_MAX_DEPTH) -> List[Dict[str, Any]]:
    root = Path(root)
    items: List[Dict[str, Any]] = []
    if not root.exists():
        return items

    def _walk(base: Path, depth: int) -> None:
        nonlocal items
        if depth > max_depth or len(items) >= max_items:
            return
        try:
            entries = list(base.iterdir())
        except Exception:
            return
        for entry in entries:
            if len(items) >= max_items:
                return
            if entry.name in _SKIP_DIRS:
                continue
            rel = entry.relative_to(root)
            if entry.is_dir():
                items.append({
                    "path": str(rel),
                    "type": "dir",
                })
                _walk(entry, depth + 1)
            elif entry.is_file():
                try:
                    size = entry.stat().st_size
                except Exception:
                    size = None
                items.append({
                    "path": str(rel),
                    "type": "file",
                    "size": size,
                })

    _walk(root, 0)
    return items


def read_resource(path: str, root: Path = ROOT_DIR, max_bytes: int = DEFAULT_MAX_BYTES) -> Tuple[bool, Dict[str, Any]]:
    if not path:
        return False, {"error": "path_required"}
    root = Path(root)
    target = Path(path)
    if not target.is_absolute():
        target = (root / target)
    if not _is_within_root(target, root):
        return False, {"error": "path_outside_root"}
    if not target.exists():
        return False, {"error": "not_found"}
    if target.is_dir():
        return True, {"path": str(target.relative_to(root)), "type": "dir", "entries": list_resources(target, max_items=DEFAULT_MAX_ITEMS, max_depth=1)}

    data = b""
    try:
        data = target.read_bytes()
    except Exception:
        return False, {"error": "read_failed"}
    truncated = len(data) > max_bytes
    if truncated:
        data = data[:max_bytes]
    text = data.decode("utf-8", errors="ignore")
    try:
        rel = str(target.relative_to(root))
    except Exception:
        rel = str(target)
    return True, {"path": rel, "type": "file", "truncated": truncated, "content": text}
