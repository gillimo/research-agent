import time
from typing import Dict, List

from researcher.state_manager import load_state, save_state


def append_worklog(kind: str, text: str, limit: int = 200) -> None:
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "kind": kind,
        "text": text,
    }
    st = load_state()
    items = st.get("worklog", [])
    if not isinstance(items, list):
        items = []
    items.append(entry)
    st["worklog"] = items[-limit:]
    save_state(st)


def read_worklog(limit: int = 10) -> List[Dict[str, str]]:
    st = load_state()
    items = st.get("worklog", [])
    if not isinstance(items, list):
        return []
    return items[-limit:]
