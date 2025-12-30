import os
import json
import hashlib
import datetime
from pathlib import Path
from typing import Any, Dict, Optional, List

from researcher import __version__

# Define common paths for the researcher project
ROOT_DIR = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT_DIR / ".researcher_state.json" # Renamed from .martin_state.json
LOG_DIR = ROOT_DIR / "logs"
LEDGER_FILE = LOG_DIR / "researcher_ledger.ndjson" # Renamed from martin_ledger.ndjson

# --- Helper functions from Martin's state management ---
def _ensure_dirs() -> None:
    """Ensures that the necessary log directory exists."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

def _now_iso() -> str:
    """Returns the current UTC time in ISO 8601 format."""
    return datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")

def _sha256_bytes(b: bytes) -> str:
    """Computes the SHA256 hash of a bytes object."""
    h = hashlib.sha256()
    h.update(b)
    return h.hexdigest()

def _read_json(path: Path, default: Any) -> Any:
    """Reads a JSON file, returning default if file not found or parsing fails."""
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        # Log error in future
        return default

def _write_json(path: Path, data: Any) -> None:
    """Writes data to a JSON file atomically."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)

# --- State management ---
DEFAULT_STATE: Dict[str, Any] = {
    "current_version": __version__,
    "session_count": 0,
    "last_session": {
        "started_at": None, "ended_at": None,
        "num_commands": 0, "last_exit_code": None,
        "summary": None,
    },
    "platform": {
        "system": os.uname().sysname if hasattr(os, "uname") else "Unknown",
        "release": os.uname().release if hasattr(os, "uname") else "Unknown",
        "python": ".".join(map(str, (os.sys.version_info.major, os.sys.version_info.minor, os.sys.version_info.micro))),
    },
    "ledger": {"entries": 0, "last_hash": None},
    "tool_ledger": {"entries": 0, "last_hash": None},
    "workspace": {"path": "./workspace", "last_file": ""}
    ,
    "tasks": []
}

def load_state() -> Dict[str, Any]:
    """Loads the agent's state from a JSON file, initializing if not found."""
    st = _read_json(STATE_FILE, DEFAULT_STATE.copy())
    # Ensure all default keys are present in the loaded state
    for k, v in DEFAULT_STATE.items():
        if k not in st:
            st[k] = v
    if "ledger" not in st: # Special handling for nested dict
        st["ledger"] = {"entries": 0, "last_hash": None}
    if "tool_ledger" not in st:
        st["tool_ledger"] = {"entries": 0, "last_hash": None}
    # Update platform info on load as it might change
    st["platform"] = DEFAULT_STATE["platform"]
    return st

def save_state(st: Dict[str, Any]) -> None:
    """Saves the agent's current state to a JSON file."""
    _write_json(STATE_FILE, st)

# --- Ledger management ---
def _ledger_entry(event: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Constructs a single ledger entry."""
    current_version = DEFAULT_STATE["current_version"]
    return {"ts": _now_iso(), "version": current_version, "event": event, "data": data}

def append_ledger(st: Dict[str, Any], entry: Dict[str, Any]) -> None:
    """Appends an entry to the ledger file with hash-chaining."""
    _ensure_dirs()
    prev_hash = st["ledger"].get("last_hash")
    payload = json.dumps(entry, ensure_ascii=False, separators=( ",", ":"))
    new_hash = _sha256_bytes(((prev_hash or "") + payload).encode("utf-8"))
    line = json.dumps({"entry": entry, "prev_hash": prev_hash, "hash": new_hash}, ensure_ascii=False)
    with open(LEDGER_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    st["ledger"]["entries"] = int(st["ledger"].get("entries", 0)) + 1
    st["ledger"]["last_hash"] = new_hash
    save_state(st)

def log_event(st: Dict[str, Any], event: str, **data: Any) -> None:
    """Logs a generic event to the ledger."""
    append_ledger(st, _ledger_entry(event, data))

# --- Session context management ---
class SessionCtx:
    """Manages the lifecycle of an agent session."""
    def __init__(self, st: Dict[str, Any]) -> None:
        self.st = st
        self.started_at = _now_iso()
        self.commands = 0
        self.last_rc: Optional[int] = None

    def begin(self) -> None:
        """Starts a new session, updating state and logging the event."""
        self.st["session_count"] = int(self.st.get("session_count", 0)) + 1
        self.st["last_session"] = {
            "started_at": self.started_at, "ended_at": None,
            "num_commands": 0, "last_exit_code": None, "summary": None
        }
        save_state(self.st)
        log_event(self.st, "session_start", started_at=self.started_at, version=DEFAULT_STATE["current_version"])

    def record_cmd(self, rc: int) -> None:
        """Records a command executed within the session."""
        self.commands += 1
        self.last_rc = rc

    def end(self) -> None:
        """Ends the current session, updating state and logging the event."""
        ended_at = _now_iso()
        summary = {"total_commands": self.commands, "last_exit_code": self.last_rc}
        self.st["last_session"] = {
            "started_at": self.started_at, "ended_at": ended_at,
            "num_commands": self.commands, "last_exit_code": self.last_rc, "summary": summary
        }
        save_state(self.st)
        log_event(self.st, "session_end", ended_at=ended_at, **summary)
