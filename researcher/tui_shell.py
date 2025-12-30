import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.live import Live

from researcher.context_harvest import gather_context
from researcher.config_loader import load_config
from researcher.state_manager import load_state, save_state
from researcher import chat_ui
from researcher.worklog import append_worklog, read_worklog
from researcher.logbook_utils import append_logbook_entry


@dataclass
class ListView:
    name: str
    items: List[Dict[str, str]]
    selected: int = 0


THEME = {
    "panel": "cyan",
    "header": "bright_cyan",
}


def _get_key() -> str:
    if os.name == "nt":
        import msvcrt
        ch = msvcrt.getch()
        if ch in (b"\x00", b"\xe0"):
            nxt = msvcrt.getch()
            arrows = {b"H": "UP", b"P": "DOWN", b"K": "LEFT", b"M": "RIGHT"}
            return arrows.get(nxt, "")
        if ch == b"\r":
            return "ENTER"
        if ch == b"\t":
            return "TAB"
        try:
            return ch.decode("utf-8")
        except Exception:
            return ""
    import sys
    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            seq = sys.stdin.read(2)
            arrows = {"[A": "UP", "[B": "DOWN", "[C": "RIGHT", "[D": "LEFT"}
            return arrows.get(seq, "ESC")
        if ch in ("\r", "\n"):
            return "ENTER"
        if ch == "\t":
            return "TAB"
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _render_tasks(tasks: List[Dict[str, str]], selected: int) -> Panel:
    table = Table(show_header=True, header_style=THEME["header"])
    table.add_column("#", style="dim", width=4)
    table.add_column("task", style="white")
    for idx, t in enumerate(tasks[:30], 1):
        style = "reverse" if idx - 1 == selected else ""
        table.add_row(str(idx), t.get("text", ""), style=style)
    return Panel(table, title="Tasks", style=THEME["panel"])


def _render_outputs(paths: List[Path], selected: int) -> Panel:
    table = Table(show_header=True, header_style=THEME["header"])
    table.add_column("#", style="dim", width=4)
    table.add_column("file", style="white")
    for idx, p in enumerate(paths[:30], 1):
        style = "reverse" if idx - 1 == selected else ""
        table.add_row(str(idx), str(p), style=style)
    return Panel(table, title="Outputs", style=THEME["panel"])


def _render_context(context: Dict[str, object], tests_last: Optional[Dict[str, object]] = None) -> Panel:
    table = Table(show_header=True, header_style=THEME["header"])
    table.add_column("key", style="dim")
    table.add_column("value", style="white")
    table.add_row("root", str(context.get("root", "")))
    table.add_row("git", str((context.get("git_status") or "").splitlines()[:1][0] if context.get("git_status") else ""))
    table.add_row("recent_files", str(len(context.get("recent_files", []) or [])))
    table.add_row("tech_stack", ", ".join(context.get("tech_stack", []) or []))
    if tests_last:
        status = "ok" if tests_last.get("ok") else "fail"
        cmd = tests_last.get("cmd", "")
        rc = tests_last.get("rc")
        dur = tests_last.get("duration_s", 0)
        table.add_row("last_test", f"{status} rc={rc} ({dur}s) {cmd}".strip())
    return Panel(table, title="Context", style=THEME["panel"])


def _render_palette(entries: List[Dict[str, str]], selected: int) -> Panel:
    table = Table(show_header=True, header_style=THEME["header"])
    table.add_column("#", style="dim", width=4)
    table.add_column("type", style="dim", width=6)
    table.add_column("value", style="white")
    for idx, entry in enumerate(entries[:30], 1):
        style = "reverse" if idx - 1 == selected else ""
        table.add_row(str(idx), entry.get("kind", ""), entry.get("value", ""), style=style)
    return Panel(table, title="Palette", style=THEME["panel"])


def _render_help() -> Panel:
    text = "Keys: q=quit, p=palette, t=tasks, o=outputs, m=process, c=context, r=refresh, f=filter outputs, tab=focus, j/k|up/down=move, a=add task, x=done task, ?=help"
    return Panel(text, title="Help", style=THEME["panel"])


def _render_output_detail(path: Optional[Path]) -> Panel:
    if not path or not path.exists():
        return Panel("No output selected.", title="Output detail", style=THEME["panel"])
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        return Panel(f"Failed to read {path} ({exc})", title="Output detail", style=THEME["panel"])
    tail = lines[-20:] if lines else []
    text = "\n".join(tail) if tail else "(empty)"
    return Panel(text, title=f"Output detail: {path.name}", style=THEME["panel"])


def _render_task_detail(task: Optional[Dict[str, str]]) -> Panel:
    if not task:
        return Panel("No task selected.", title="Task detail", style=THEME["panel"])
    text = task.get("text", "")
    ts = task.get("ts", "")
    detail = f"{text}\n\ncreated: {ts}" if ts else text
    return Panel(detail, title="Task detail", style=THEME["panel"])


def _render_worklog(entries: List[Dict[str, str]], selected: int) -> Panel:
    table = Table(show_header=True, header_style=THEME["header"])
    table.add_column("#", style="dim", width=4)
    table.add_column("kind", style="dim", width=10)
    table.add_column("text", style="white")
    for idx, entry in enumerate(entries[:20], 1):
        style = "reverse" if idx - 1 == selected else ""
        table.add_row(str(idx), entry.get("kind", ""), entry.get("text", ""), style=style)
    return Panel(table, title="Process", style=THEME["panel"])


def _render_worklog_footer(entries: List[Dict[str, str]]) -> Panel:
    table = Table(show_header=True, header_style=THEME["header"])
    table.add_column("#", style="dim", width=4)
    table.add_column("kind", style="dim", width=10)
    table.add_column("text", style="white")
    for idx, entry in enumerate(entries[:5], 1):
        table.add_row(str(idx), entry.get("kind", ""), entry.get("text", ""))
    return Panel(table, title="Heartbeat", style=THEME["panel"])


def _build_layout(header: Panel, left: Panel, right: Panel, process: Panel, footer: Panel) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(header, name="header", size=3),
        Layout(name="body", ratio=1),
        Layout(process, name="process", size=7),
        Layout(footer, name="footer", size=3),
    )
    layout["body"].split_row(
        Layout(left, name="left", ratio=2),
        Layout(right, name="right", ratio=3),
    )
    return layout


def _clamp_selection(selected: int, items: List[object]) -> int:
    if not items:
        return 0
    return max(0, min(selected, len(items) - 1))


def _prompt_input(live: Live, console: Console, prompt: str) -> str:
    live.stop()
    try:
        return console.input(prompt)
    finally:
        live.start()


def _load_tasks(st: Dict[str, object]) -> List[Dict[str, str]]:
    tasks = st.get("tasks", [])
    return tasks if isinstance(tasks, list) else []


def _save_tasks(st: Dict[str, object], tasks: List[Dict[str, str]]) -> None:
    st["tasks"] = tasks
    st.pop("tasks_prompted", None)
    save_state(st)


def _ensure_handle(console: Console) -> str:
    st = load_state()
    handle = ""
    if isinstance(st, dict):
        handle = st.get("operator_handle", "") or ""
    if not handle:
        try:
            entered = console.input("Handle for logbook? (enter for user) ").strip()
        except Exception:
            entered = ""
        handle = entered or "user"
        if isinstance(st, dict):
            st["operator_handle"] = handle
            save_state(st)
    return handle


def _prompt_clock(console: Console, action: str) -> None:
    handle = _ensure_handle(console)
    note = f"auto: {action.lower()}"
    append_logbook_entry(handle, action, note)
    append_worklog("doing", f"{action} recorded (auto)")


def _mo_preflight(console: Console) -> None:
    root = Path.cwd()
    checks = []
    checks.append(("tickets", "ok" if (root / "docs" / "tickets.md").exists() else "missing"))
    checks.append(("bug_log", "ok" if (root / "docs" / "bug_log.md").exists() else "missing"))
    checks.append(("logbook", "ok" if (root / "docs" / "logbook.md").exists() else "missing"))
    st = load_state()
    last_test = st.get("tests_last", {}) if isinstance(st, dict) else {}
    if last_test:
        checks.append(("last_test", f"{'ok' if last_test.get('ok') else 'fail'} {last_test.get('ts','')}"))
    else:
        checks.append(("last_test", "none (run /tests)"))
    console.print("Preflight checks:")
    for key, val in checks:
        console.print(f"- {key}: {val}")
    append_worklog("plan", "tui preflight checks complete")


def run_tui() -> None:
    console = Console()
    _mo_preflight(console)
    _prompt_clock(console, "Clock-in")
    st = load_state()
    root = Path.cwd()
    context = gather_context(root, max_recent=10)
    palette_entries = chat_ui.build_palette_entries("", chat_ui.get_slash_commands(), [])
    palette_items = [{"kind": kind, "value": value} for kind, value in palette_entries]
    tasks = _load_tasks(st)
    outputs_dir = root / "logs" / "outputs"
    outputs = sorted(outputs_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True) if outputs_dir.exists() else []
    last_cmd = st.get("last_command_summary", {}) or {}
    tests_last = st.get("tests_last", {}) if isinstance(st, dict) else {}
    header = Panel("Martin TUI", title="Martin", style=THEME["panel"])
    footer = _render_help()
    view = "palette"
    focus = "left"
    help_mode = False
    selections = {"palette": 0, "tasks": 0, "outputs": 0}
    outputs_filter = ""
    banner_mode = "tui"
    cfg = load_config()
    model_info = str(cfg.get("local_model") or cfg.get("embedding_model") or "local")
    warn = "local-only" if cfg.get("local_only") else ""
    current_host = st.get("current_host", "") if isinstance(st, dict) else ""
    chat_ui.render_status_banner(context, last_cmd, mode=banner_mode, model_info=model_info, warnings=warn, current_host=current_host)
    last_heartbeat = time.monotonic()
    heartbeat_panel = _render_worklog_footer(read_worklog(5))

    with Live(_build_layout(header, _render_palette(palette_items, selections["palette"]), _render_context(context, tests_last), heartbeat_panel, footer), console=console, refresh_per_second=4) as live:
        while True:
            key = _get_key()
            if key == "q":
                break
            if key == "?":
                help_mode = not help_mode
            elif key in ("p",):
                view = "palette"
            elif key in ("t",):
                view = "tasks"
            elif key in ("o",):
                view = "outputs"
            elif key in ("m",):
                view = "process"
            elif key in ("c",):
                context = gather_context(root, max_recent=10)
            elif key in ("r",):
                context = gather_context(root, max_recent=10)
            elif key in ("TAB",):
                focus = "right" if focus == "left" else "left"
            elif key in ("j", "DOWN"):
                selections[view] = _clamp_selection(selections[view] + 1, {"palette": palette_items, "tasks": tasks, "outputs": outputs}[view])
            elif key in ("k", "UP"):
                selections[view] = _clamp_selection(selections[view] - 1, {"palette": palette_items, "tasks": tasks, "outputs": outputs}[view])
            elif key in ("a",) and view == "tasks":
                text = _prompt_input(live, console, "Add task: ").strip()
                if text:
                    tasks.append({"text": text, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
                    _save_tasks(st, tasks[-100:])
            elif key in ("x",) and view == "tasks":
                tasks = _load_tasks(st)
                idx = selections["tasks"]
                if 0 <= idx < len(tasks):
                    tasks.pop(idx)
                    _save_tasks(st, tasks)
                    selections["tasks"] = _clamp_selection(selections["tasks"], tasks)
            elif key in ("f",) and view == "outputs":
                outputs_filter = _prompt_input(live, console, "Filter outputs (empty=clear): ").strip()

            now = time.monotonic()
            if now - last_heartbeat >= 30:
                append_worklog("heartbeat", "tui idle")
                last_heartbeat = now

            st = load_state()
            tests_last = st.get("tests_last", {}) if isinstance(st, dict) else {}
            if view == "palette":
                palette_entries = chat_ui.build_palette_entries("", chat_ui.get_slash_commands(), [])
                palette_items = [{"kind": kind, "value": value} for kind, value in palette_entries]
                selections["palette"] = _clamp_selection(selections["palette"], palette_items)
                left = _render_palette(palette_items, selections["palette"])
                right = _render_help() if help_mode else _render_context(context, tests_last)
            elif view == "tasks":
                tasks = _load_tasks(st)
                selections["tasks"] = _clamp_selection(selections["tasks"], tasks)
                left = _render_tasks(tasks, selections["tasks"])
                task = tasks[selections["tasks"]] if tasks else None
                right = _render_help() if help_mode else _render_task_detail(task)
            elif view == "process":
                entries = read_worklog(10)
                selections.setdefault("process", 0)
                selections["process"] = _clamp_selection(selections["process"], entries)
                left = _render_worklog(entries, selections["process"])
                right = _render_help() if help_mode else _render_context(context, tests_last)
            else:
                outputs = sorted(outputs_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True) if outputs_dir.exists() else []
                if outputs_filter:
                    outputs = [p for p in outputs if outputs_filter.lower() in str(p).lower()]
                selections["outputs"] = _clamp_selection(selections["outputs"], outputs)
                left = _render_outputs(outputs, selections["outputs"])
                out = outputs[selections["outputs"]] if outputs else None
                right = _render_help() if help_mode else _render_output_detail(out)

            heartbeat_panel = _render_worklog_footer(read_worklog(5))
            footer = _render_help()
            live.update(_build_layout(header, left, right, heartbeat_panel, footer))
            time.sleep(0.05)
    _prompt_clock(console, "Clock-out")
