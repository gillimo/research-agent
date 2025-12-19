import subprocess
from pathlib import Path
from typing import Dict, Any, List


def _run(cmd: List[str], cwd: Path) -> str:
    try:
        out = subprocess.check_output(cmd, cwd=cwd, stderr=subprocess.STDOUT, text=True)
        return out.strip()
    except Exception:
        return ""


def gather_context(root: Path, max_recent: int = 10) -> Dict[str, Any]:
    root = root.resolve()
    git_status = _run(["git", "status", "-sb"], root)
    git_diff = _run(["git", "diff", "--stat"], root)
    recent = []
    try:
        for p in sorted(root.rglob("*"), key=lambda x: x.stat().st_mtime, reverse=True)[:max_recent]:
            if p.is_file():
                recent.append(str(p))
    except Exception:
        pass
    return {
        "root": str(root),
        "git_status": git_status,
        "git_diff_stat": git_diff,
        "recent_files": recent,
    }
