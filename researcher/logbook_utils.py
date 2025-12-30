import time
from pathlib import Path
from typing import Optional


LOGBOOK_PATH = Path("docs") / "logbook.md"


def _ensure_logbook() -> None:
    if LOGBOOK_PATH.exists():
        return
    LOGBOOK_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOGBOOK_PATH.write_text("Logbook\n=======\n\nEntries (newest first)\n", encoding="utf-8")


def append_logbook_entry(handle: str, action: str, note: str, skipped_reason: Optional[str] = None) -> None:
    _ensure_logbook()
    stamp = time.strftime("%Y-%m-%d", time.gmtime())
    if skipped_reason:
        note = f"skipped ({skipped_reason})"
    line = f"- {stamp} | handle: {handle} | {action}: {note}"
    try:
        lines = LOGBOOK_PATH.read_text(encoding="utf-8").splitlines()
    except Exception:
        lines = ["Logbook", "=======", "", "Entries (newest first)"]
    out = []
    inserted = False
    for idx, ln in enumerate(lines):
        out.append(ln)
        if not inserted and ln.strip().lower().startswith("entries"):
            out.append(line)
            inserted = True
    if not inserted:
        out.append("")
        out.append("Entries (newest first)")
        out.append(line)
    LOGBOOK_PATH.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
