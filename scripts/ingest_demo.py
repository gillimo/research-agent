#!/usr/bin/env python
import argparse
from pathlib import Path

from researcher.config_loader import load_config, ensure_dirs
from researcher.index_utils import load_index_from_config
from researcher.ingester import ingest_files


def _clear_index(cfg) -> None:
    vs = cfg.get("vector_store", {}) or {}
    index_path = Path(vs.get("index_path", "data/index/faiss.index"))
    mock_path = Path(vs.get("mock_index_path", "data/index/mock_index.pkl"))
    meta_path = index_path.with_suffix(".meta.pkl")
    for p in (index_path, meta_path, mock_path):
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Idempotent ingest demo for sample docs")
    parser.add_argument(
        "--path",
        default="data/sample/readme.txt",
        help="Path to a file or directory to ingest",
    )
    parser.add_argument(
        "--simple-index",
        action="store_true",
        help="Force SimpleIndex (skip FAISS)",
    )
    parser.add_argument(
        "--no-clear",
        action="store_true",
        help="Do not clear existing index before ingest",
    )
    args = parser.parse_args()

    cfg = load_config()
    if args.simple_index:
        cfg.setdefault("vector_store", {})
        cfg["vector_store"]["type"] = "simple"

    ensure_dirs(cfg)
    if not args.no_clear:
        _clear_index(cfg)

    target = Path(args.path)
    if target.is_dir():
        files = [p for p in target.rglob("*") if p.is_file()]
    else:
        files = [target]
    files = [p for p in files if p.exists()]
    if not files:
        print("No files found to ingest.")
        return 1

    idx = load_index_from_config(cfg)
    result = ingest_files(idx, files)
    if hasattr(idx, "save"):
        idx.save()
    print(f"Ingested {result.get('ingested', 0)} files; errors: {len(result.get('errors', []))}")
    for err in result.get("errors", []):
        print(f"error: {err}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
