import time
from pathlib import Path
from typing import Optional


def last_log_timestamp(log_path: Path) -> Optional[float]:
    if not log_path.exists():
        return None
    try:
        return log_path.stat().st_mtime
    except Exception:
        return None


def needs_nudge(log_path: Path, idle_seconds: int = 300) -> bool:
    ts = last_log_timestamp(log_path)
    if ts is None:
        return True
    return (time.time() - ts) > idle_seconds


def nudge_message(log_path: Path, idle_seconds: int = 300) -> str:
    if needs_nudge(log_path, idle_seconds):
        return f"Agent idle > {idle_seconds}s per {log_path}; prompt to continue."
    return "Agent active."
