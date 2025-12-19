#!/usr/bin/env python
import argparse
import json
import time
from pathlib import Path

LOG_PATH = Path("logs/questions.ndjson")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _ensure_log_dir() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


def _next_id() -> int:
    if not LOG_PATH.exists():
        return 1
    last_id = 0
    try:
        with LOG_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    last_id = max(last_id, int(entry.get("id", 0)))
                except Exception:
                    continue
    except Exception:
        pass
    return last_id + 1


def log_open(text: str) -> int:
    _ensure_log_dir()
    entry = {
        "id": _next_id(),
        "ts": _now_iso(),
        "status": "open",
        "text": text.strip(),
    }
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry["id"]


def log_close(entry_id: int) -> bool:
    _ensure_log_dir()
    entry = {
        "id": entry_id,
        "ts": _now_iso(),
        "status": "closed",
    }
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Log or close blocker questions")
    parser.add_argument("--text", help="Question/blocker text to log")
    parser.add_argument("--close", type=int, help="Close a question by id")
    args = parser.parse_args()

    if args.text:
        qid = log_open(args.text)
        print(f"logged question id={qid}")
        return 0
    if args.close:
        log_close(args.close)
        print(f"closed question id={args.close}")
        return 0

    print("Provide --text to log or --close <id> to close.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
