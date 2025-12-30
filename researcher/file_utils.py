import difflib
import re
from pathlib import Path
from typing import List, Optional


def _ask_yes_no(prompt: str) -> bool:
    try:
        ans = input(f"\033[93mmartin: {prompt} (y/n)\033[0m ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        ans = "n"
    return ans == "y"


_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _extract_hunk_lines(diff_lines: List[str]) -> List[int]:
    lines: List[int] = []
    for line in diff_lines:
        m = _HUNK_RE.match(line)
        if not m:
            continue
        try:
            lines.append(int(m.group(1)))
        except Exception:
            continue
    return lines


def print_quick_open_hints(path: Path, diff_lines: List[str], limit: int = 5) -> None:
    hints = _extract_hunk_lines(diff_lines)
    if not hints:
        return
    print("\033[96mmartin: Quick-open hints\033[0m")
    for line_no in hints[:limit]:
        print(f"/open {path}:{line_no}")


def render_snippet(path: Path, line_no: Optional[int], context: int = 3) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        return f"martin: Failed to read {path} ({exc})"
    if not lines:
        return f"martin: {path} is empty."
    if line_no is None or line_no <= 0:
        line_no = 1
    start = max(line_no - context - 1, 0)
    end = min(line_no + context, len(lines))
    out = []
    for idx in range(start, end):
        marker = ">" if idx + 1 == line_no else " "
        out.append(f"{marker} {idx + 1:4d} | {lines[idx]}")
    return "\n".join(out)


def preview_write(path: Path, content: str) -> bool:
    """Preview a unified diff if the file exists; return True if write is approved."""
    if not path.exists():
        return True
    try:
        before = path.read_text(encoding="utf-8")
    except Exception:
        before = ""
    diff_lines = list(difflib.unified_diff(
        before.splitlines(),
        content.splitlines(),
        fromfile=str(path),
        tofile=str(path),
        lineterm="",
    ))
    if diff_lines:
        print("\033[96mmartin: Diff preview\033[0m")
        print("\n".join(diff_lines[:200]))
        print_quick_open_hints(path, diff_lines)
    return _ask_yes_no("Apply changes to existing file?")
