import re
import subprocess
import time
from typing import List, Tuple

from researcher.sanitize import sanitize_prompt

CMD_LINE_RE = re.compile(r"(?im)^\s*command:\s*(.+?)\s*$")


def extract_commands(text: str) -> List[str]:
    cmds = []
    cwd = None
    for line in text.splitlines():
        m = CMD_LINE_RE.match(line)
        if not m:
            continue
        c = m.group(1).strip().strip("`")
        if " | " in c:
            c = c.split(" | ", 1)[0].strip()
        if c.startswith("cd "):
            cwd = c[3:].strip()
            cmds.append(c)
        else:
            cmds.append(f"cd {cwd} && {c}" if cwd else c)
    return cmds


def run_plan(commands: List[str], timeout: int = 120) -> List[Tuple[str, int, str]]:
    """Run commands non-interactively; return list of (cmd, rc, output)."""
    results = []
    for cmd in commands:
        start = time.time()
        try:
            proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
            out = (proc.stdout or "") + (proc.stderr or "")
            results.append((cmd, proc.returncode, out.strip()))
        except subprocess.TimeoutExpired:
            results.append((cmd, -1, f"Timed out after {timeout}s"))
        except Exception as e:
            results.append((cmd, -1, str(e)))
    return results


def sanitize_and_extract(text: str) -> Tuple[str, bool, List[str]]:
    sanitized, changed = sanitize_prompt(text)
    cmds = extract_commands(sanitized)
    return sanitized, changed, cmds
