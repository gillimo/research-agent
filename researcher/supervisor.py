import datetime
import json
import time
from pathlib import Path
from typing import Optional, Tuple

from researcher.state_manager import LEDGER_FILE, load_state, log_event


def last_log_timestamp(log_path: Path) -> Optional[float]:
    if not log_path.exists():
        return None
    try:
        return log_path.stat().st_mtime
    except Exception:
        return None

def _parse_ts(ts: str) -> Optional[float]:
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.datetime.fromisoformat(ts).timestamp()
    except Exception:
        return None

def last_ledger_entry(ledger_path: Path) -> Tuple[Optional[float], Optional[str]]:
    if not ledger_path.exists():
        return (None, None)
    try:
        with ledger_path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return (None, None)
            read_size = min(8192, size)
            f.seek(-read_size, 2)
            data = f.read().decode(errors="ignore")
        lines = [ln for ln in data.splitlines() if ln.strip()]
        if not lines:
            return (None, None)
        last = json.loads(lines[-1])
        entry = last.get("entry", {})
        ts = _parse_ts(entry.get("ts", "")) if isinstance(entry, dict) else None
        ev = entry.get("event") if isinstance(entry, dict) else None
        return (ts, ev)
    except Exception:
        return (None, None)

def needs_nudge(log_path: Path, idle_seconds: int = 300) -> bool:
    ts, _ev = last_ledger_entry(LEDGER_FILE)
    if ts is None:
        ts = last_log_timestamp(log_path)
    if ts is None:
        return True
    return (time.time() - ts) > idle_seconds


def nudge_message(log_path: Path, idle_seconds: int = 300) -> str:
    ts, ev = last_ledger_entry(LEDGER_FILE)
    if ts is None:
        ts = last_log_timestamp(log_path)
    if ts is None:
        return f"Agent idle > {idle_seconds}s; no ledger/log activity found."
    age = time.time() - ts
    if age > idle_seconds:
        ev_txt = f" last event: {ev}" if ev else ""
        return f"Agent idle > {idle_seconds}s;{ev_txt} ({int(age)}s ago)."
    ev_txt = f" last event: {ev}" if ev else ""
    return f"Agent active;{ev_txt} ({int(age)}s ago)."


def run_supervisor(
    logs_path: Path,
    idle_seconds: int = 300,
    sleep_seconds: int = 30,
    prompt: str = "Agent appears idle; please continue or report status.",
    max_prompts: int = 3,
) -> None:
    """
    Periodically checks for idle activity and prints a prompt when idle.
    Exits after max_prompts (0 = unlimited).
    """
    prompts_sent = 0
    while True:
        if needs_nudge(logs_path, idle_seconds=idle_seconds):
            print(prompt)
            try:
                st = load_state()
                log_event(st, "supervisor_prompt", prompt=prompt)
            except Exception:
                pass
            prompts_sent += 1
            if max_prompts and prompts_sent >= max_prompts:
                break
        time.sleep(max(1, sleep_seconds))
