import json
import hashlib
import datetime
from pathlib import Path
from typing import Any, Dict, Optional, List

from researcher import sanitize
from researcher.state_manager import ROOT_DIR, load_state, save_state

TOOL_LEDGER_FILE = ROOT_DIR / "logs" / "tool_ledger.ndjson"


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")


def _sha256_bytes(b: bytes) -> str:
    h = hashlib.sha256()
    h.update(b)
    return h.hexdigest()


def _ensure_log_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _summarize_text(text: str, max_chars: int = 2000) -> Dict[str, Any]:
    if not text:
        return {"text": "", "lines": 0, "chars": 0, "truncated": False}
    lines = text.splitlines()
    if len(text) <= max_chars:
        return {"text": text, "lines": len(lines), "chars": len(text), "truncated": False}
    head = text[:max_chars].rstrip()
    return {"text": head, "lines": len(lines), "chars": len(text), "truncated": True}


def _sanitize_text(text: str) -> Dict[str, Any]:
    if not text:
        return {"text": "", "changed": False}
    cleaned, changed = sanitize.sanitize_prompt(text)
    return {"text": cleaned, "changed": changed}


def append_tool_entry(
    entry: Dict[str, Any],
    st: Optional[Dict[str, Any]] = None,
    ledger_path: Optional[Path] = None
) -> None:
    state = st or load_state()
    path = ledger_path or TOOL_LEDGER_FILE
    _ensure_log_dir(path)

    command_raw = entry.get("command", "") or ""
    cmd_sanitized = _sanitize_text(command_raw)
    cmd_hash = _sha256_bytes(command_raw.encode("utf-8")) if command_raw else ""

    stdout_raw = entry.get("stdout", "") or ""
    stderr_raw = entry.get("stderr", "") or ""
    out_sanitized = _sanitize_text(stdout_raw)
    err_sanitized = _sanitize_text(stderr_raw)
    out_summary = _summarize_text(out_sanitized["text"])
    err_summary = _summarize_text(err_sanitized["text"])

    payload = {
        "ts": _now_iso(),
        "cwd": entry.get("cwd", ""),
        "command": cmd_sanitized["text"],
        "command_hash": cmd_hash,
        "rc": entry.get("rc", None),
        "ok": entry.get("ok", None),
        "duration_s": entry.get("duration_s", None),
        "stdout": out_summary,
        "stderr": err_summary,
        "output_path": entry.get("output_path", ""),
        "risk": entry.get("risk", ""),
        "risk_reasons": entry.get("risk_reasons", []),
        "sandbox_mode": entry.get("sandbox_mode", ""),
        "approval_policy": entry.get("approval_policy", ""),
        "sanitized": {
            "command": cmd_sanitized["changed"],
            "stdout": out_sanitized["changed"],
            "stderr": err_sanitized["changed"],
        },
    }

    prev_hash = state.get("tool_ledger", {}).get("last_hash")
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    new_hash = _sha256_bytes(((prev_hash or "") + raw).encode("utf-8"))
    line = json.dumps({"entry": payload, "prev_hash": prev_hash, "hash": new_hash}, ensure_ascii=False)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")

    state.setdefault("tool_ledger", {"entries": 0, "last_hash": None})
    state["tool_ledger"]["entries"] = int(state["tool_ledger"].get("entries", 0)) + 1
    state["tool_ledger"]["last_hash"] = new_hash
    if st is None:
        save_state(state)


def read_recent(limit: int = 10, ledger_path: Optional[Path] = None, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    path = ledger_path or TOOL_LEDGER_FILE
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    filters = filters or {}
    rc_filter = filters.get("rc")
    rc_not = filters.get("rc_not")
    risk_filter = filters.get("risk")
    cwd_filter = filters.get("cwd")
    text_filter = filters.get("text")
    since_ts = filters.get("since")
    for line in lines[::-1]:
        try:
            row = json.loads(line)
        except Exception:
            continue
        entry = row.get("entry", {})
        if since_ts and entry.get("ts", "") <= since_ts:
            continue
        if rc_filter is not None and entry.get("rc") != rc_filter:
            continue
        if rc_not is not None and entry.get("rc") == rc_not:
            continue
        if risk_filter and entry.get("risk") != risk_filter:
            continue
        if cwd_filter and cwd_filter not in (entry.get("cwd") or ""):
            continue
        if text_filter and text_filter not in (entry.get("command") or ""):
            continue
        out.append(row)
        if len(out) >= limit:
            break
    return list(reversed(out))


def export_json(path: Path, limit: int = 200, ledger_path: Optional[Path] = None) -> Path:
    entries = read_recent(limit=limit, ledger_path=ledger_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
