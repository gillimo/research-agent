import subprocess
import shutil
from pathlib import Path
from typing import Dict, Any, List


def _run(cmd: List[str], cwd: Path) -> str:
    try:
        out = subprocess.check_output(cmd, cwd=cwd, stderr=subprocess.STDOUT, text=True)
        return out.strip()
    except Exception:
        return ""


def _should_skip(path: Path) -> bool:
    parts = {p.lower() for p in path.parts}
    return any(p in parts for p in {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build"})


def _tree_snapshot(root: Path, max_depth: int = 2, max_entries: int = 200) -> List[str]:
    items: List[str] = []
    root = root.resolve()
    for p in root.rglob("*"):
        try:
            rel = p.relative_to(root)
        except Exception:
            continue
        if _should_skip(p):
            continue
        if len(rel.parts) > max_depth:
            continue
        items.append(str(rel))
        if len(items) >= max_entries:
            break
    return items


def _language_summary(root: Path, max_files: int = 500) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    seen = 0
    for p in root.rglob("*"):
        if _should_skip(p):
            continue
        if not p.is_file():
            continue
        ext = p.suffix.lower().lstrip(".") or "no_ext"
        counts[ext] = counts.get(ext, 0) + 1
        seen += 1
        if seen >= max_files:
            break
    return counts


def _detect_stack(root: Path) -> List[str]:
    markers = {
        "python": ["pyproject.toml", "requirements.txt", "setup.cfg"],
        "node": ["package.json", "pnpm-lock.yaml", "yarn.lock"],
        "go": ["go.mod"],
        "rust": ["Cargo.toml"],
        "dotnet": ["*.csproj", "*.fsproj", "*.sln"],
        "java": ["pom.xml", "build.gradle", "build.gradle.kts"],
    }
    detected = []
    for stack, files in markers.items():
        for name in files:
            if "*" in name:
                if list(root.rglob(name)):
                    detected.append(stack)
                    break
            else:
                if (root / name).exists():
                    detected.append(stack)
                    break
    return sorted(set(detected))


def _open_prs(root: Path) -> Dict[str, Any]:
    if not shutil.which("gh"):
        return {"items": [], "note": "gh not available"}
    raw = _run(["gh", "pr", "list", "--limit", "5", "--json", "number,title,state"], root)
    if not raw:
        return {"items": [], "note": "no data"}
    try:
        import json
        return {"items": json.loads(raw), "note": ""}
    except Exception:
        return {"items": [], "note": "parse error"}


def gather_context(root: Path, max_recent: int = 10) -> Dict[str, Any]:
    root = root.resolve()
    git_status = _run(["git", "status", "-sb"], root)
    git_diff = _run(["git", "diff", "--stat"], root)
    recent = []
    try:
        for p in sorted(root.rglob("*"), key=lambda x: x.stat().st_mtime, reverse=True):
            if _should_skip(p):
                continue
            if p.is_file():
                try:
                    recent.append(str(p.relative_to(root)))
                except Exception:
                    recent.append(str(p))
            if len(recent) >= max_recent:
                break
    except Exception:
        pass
    return {
        "root": str(root),
        "git_status": git_status,
        "git_diff_stat": git_diff,
        "recent_files": recent,
        "tree": _tree_snapshot(root),
        "languages": _language_summary(root),
        "tech_stack": _detect_stack(root),
        "open_prs": _open_prs(root),
    }
