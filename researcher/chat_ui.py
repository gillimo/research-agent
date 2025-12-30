import os
import glob
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def get_slash_commands() -> List[str]:
    return [
        "/help", "/clear", "/status", "/memory", "/history", "/palette", "/context", "/plan", "/outputs", "/resume", "/librarian", "/tasks",
        "/abilities", "/resources", "/resource", "/tests", "/rag",
        "/agent", "/cloud", "/ask", "/ingest", "/compress", "/signoff", "/exit", "/catalog", "/review",
    ]


def get_command_descriptions() -> Dict[str, str]:
    return {
        "/help": "show commands",
        "/clear": "clear transcript",
        "/status": "show status JSON",
        "/memory": "show memory snapshots",
        "/history": "show recent inputs",
        "/palette": "command palette + recent inputs",
        "/context": "show context pack",
        "/plan": "show last plan",
        "/outputs": "list saved outputs",
        "/resume": "show resume snapshot",
        "/librarian": "inbox/request/sources",
        "/tasks": "task queue",
        "/abilities": "list internal abilities",
        "/resources": "list readable resources",
        "/resource": "read a resource",
        "/tests": "suggest tests",
        "/rag": "rag status",
        "/agent": "agent mode toggle",
        "/cloud": "cloud mode toggle",
        "/ask": "ask local RAG",
        "/ingest": "ingest paths",
        "/compress": "compress transcript",
        "/signoff": "signoff summary",
        "/exit": "exit chat",
        "/catalog": "librarian catalog",
        "/review": "review mode toggle",
    }


def _fuzzy_match(needle: str, hay: str) -> bool:
    if not needle:
        return True
    it = iter(hay)
    for ch in needle:
        for h in it:
            if h == ch:
                break
        else:
            return False
    return True


def setup_readline(cfg: Dict[str, object], slash_commands: List[str]) -> Tuple[Optional[object], Optional[Path]]:
    try:
        import readline as _readline
    except Exception:
        return None, None

    def completer(_text: str, state: int) -> Optional[str]:
        buffer = _readline.get_line_buffer()
        if buffer.startswith("/"):
            prefix = buffer
            matches = [c for c in slash_commands if c.startswith(prefix)]
            if not matches:
                needle = buffer.lstrip("/").lower()
                matches = [c for c in slash_commands if _fuzzy_match(needle, c.lower())]
            if state < len(matches):
                return matches[state]
        # Path completion (simple): complete last token when it looks like a path.
        last = buffer.split()[-1] if buffer.split() else ""
        if last and (last.startswith(("~", ".", "/", "\\")) or ":" in last):
            expanded = os.path.expandvars(os.path.expanduser(last))
            pattern = expanded + "*"
            hits = sorted(glob.glob(pattern))
            if hits:
                results = []
                for h in hits:
                    if last.startswith("~"):
                        home = os.path.expanduser("~")
                        if h.startswith(home):
                            h = "~" + h[len(home):]
                    results.append(h)
                if state < len(results):
                    return results[state]
        return None

    _readline.set_completer(completer)
    try:
        _readline.parse_and_bind("tab: complete")
        _readline.parse_and_bind("set show-all-if-ambiguous on")
        _readline.parse_and_bind("set completion-ignore-case on")
        _readline.parse_and_bind("\"\\e[A\": history-search-backward")
        _readline.parse_and_bind("\"\\e[B\": history-search-forward")
    except Exception:
        pass

    history_path = None
    try:
        logs_dir = Path(cfg.get("data_paths", {}).get("logs", "logs"))
        logs_dir.mkdir(parents=True, exist_ok=True)
        history_path = logs_dir / "martin_history.txt"
        if history_path.exists():
            _readline.read_history_file(str(history_path))
        _readline.set_history_length(1000)
    except Exception:
        history_path = None

    return _readline, history_path


def print_context_summary(payload: Dict[str, object]) -> None:
    try:
        root = payload.get("root", "")
        root_name = Path(root).name if root else "repo"
        recent = payload.get("recent_files", []) or []
        git_status = (payload.get("git_status") or "").splitlines()
        git_line = git_status[0] if git_status else "git status unavailable"
        print(f"martin: Context: {root_name} | recent_files={len(recent)} | {git_line}")
    except Exception:
        pass


def shorten_output(text: str, max_len: int = 400) -> str:
    s = (text or "").strip().replace("\r", " ")
    if len(s) <= max_len:
        return s
    return s[:max_len].rstrip() + "..."


def render_palette(query: str, slash_commands: List[str], command_descriptions: Dict[str, str], session_transcript: List[str]) -> None:
    cmd_matches = [c for c in slash_commands if _fuzzy_match(query, c.lower())]
    history_inputs = [ln for ln in session_transcript if ln.startswith("You: ")]
    if query:
        hist_matches = [ln for ln in history_inputs if query in ln.lower()]
    else:
        hist_matches = history_inputs[-10:]
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table
        console = Console()
        panels = []
        if cmd_matches:
            table = Table(title="Commands", show_header=True, header_style="cyan")
            table.add_column("cmd", style="white")
            table.add_column("desc", style="dim")
            for c in cmd_matches[:20]:
                table.add_row(c, command_descriptions.get(c, ""))
            panels.append(Panel(table, title="Palette"))
        if hist_matches:
            table = Table(title="Recent inputs", show_header=False)
            table.add_column("input", style="white")
            for ln in hist_matches[-10:]:
                table.add_row(ln)
            panels.append(Panel(table, title="History"))
        if panels:
            for p in panels:
                console.print(p)
            return
    except Exception:
        pass
    print("martin: Command palette")
    if cmd_matches:
        print("Commands:")
        for c in cmd_matches[:20]:
            desc = command_descriptions.get(c, "")
            suffix = f" - {desc}" if desc else ""
            print(f"- {c}{suffix}")
    if hist_matches:
        print("Recent inputs:")
        for ln in hist_matches[-10:]:
            print(ln)
    if not cmd_matches and not hist_matches:
        print("martin: No matches.")


def render_history(lines: List[str], title: str = "Recent input history") -> None:
    try:
        from rich.console import Console
        from rich.table import Table
        console = Console()
        table = Table(title=title, show_header=True, header_style="cyan")
        table.add_column("#", style="dim", width=4)
        table.add_column("input", style="white")
        for idx, ln in enumerate(lines, 1):
            table.add_row(str(idx), ln)
        console.print(table)
        return
    except Exception:
        pass
    print(f"martin: {title}:")
    for idx, ln in enumerate(lines, 1):
        print(f"{idx}. {ln}")


def handle_history_command(
    args: List[str],
    session_transcript: List[str],
    readline_mod: Optional[object],
    history_path: Optional[Path],
) -> Optional[str]:
    sub = args[0].lower() if args else ""
    if sub == "clear":
        cleared = False
        try:
            if readline_mod:
                readline_mod.clear_history()
                cleared = True
        except Exception:
            pass
        try:
            if history_path and history_path.exists():
                history_path.write_text("", encoding="utf-8")
                cleared = True
        except Exception:
            pass
        print("martin: History cleared." if cleared else "martin: Unable to clear history.")
        return None
    if sub == "find":
        query = " ".join(args[1:]).strip().lower()
        if not query:
            print("martin: Provide text to search.")
            return None
        lines = [ln for ln in session_transcript if ln.startswith("You: ") and query in ln.lower()]
        if not lines:
            print("martin: No matching inputs.")
            return None
        render_history(lines[-20:], title="Matching input history")
        return None
    if sub == "pick":
        try:
            idx = int(args[1]) if len(args) > 1 else 0
        except Exception:
            idx = 0
        lines = [ln for ln in session_transcript if ln.startswith("You: ")]
        if not lines:
            print("martin: No input history captured.")
            return None
        window = lines[-20:]
        if not (1 <= idx <= len(window)):
            print("martin: Use /history pick <n> from the last 20 entries.")
            return None
        picked = window[idx - 1]
        print(f"martin: Picked input: {picked}")
        print("martin: Press Up arrow to edit/reuse.")
        return picked.replace("You: ", "", 1)
    try:
        limit = int(args[0]) if args else 20
    except Exception:
        limit = 20
    lines = [ln for ln in session_transcript if ln.startswith("You: ")]
    if not lines:
        print("martin: No input history captured.")
        return None
    render_history(lines[-limit:], title="Recent input history")
    return None
