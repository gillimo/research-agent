import argparse
import builtins
import datetime
import logging
import os
import re
import sys
import time
import json # Added for main loop
import traceback
import threading
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

if __package__ is None and __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from researcher import sanitize
from researcher.config_loader import load_config, ensure_dirs
from researcher.index import SimpleIndex, FaissIndex
from researcher.index_utils import save_index_from_config
from researcher.ingester import ingest_files
from researcher.log_utils import setup_logger
from researcher.provenance import build_response
from researcher.answer import compose_answer
# Removed: from researcher.martin_behaviors import sanitize_and_extract, run_plan
from researcher.supervisor import nudge_message
from researcher.local_llm import run_ollama_chat, run_ollama_chat_stream, check_ollama_health
from researcher import chat_ui
from researcher.tui_shell import run_tui
from researcher.remote_transport import start_tunnel, stop_tunnel, status_tunnel, validate_transport
from researcher.system_context import get_system_context

# New imports for Librarian client
from researcher.librarian_client import LibrarianClient
from researcher.socket_server import SocketServer
from researcher.socket_test_bridge import TestSocketBridge

# New imports for Martin's main loop
from researcher.state_manager import load_state, save_state, log_event, SessionCtx, ROOT_DIR, LEDGER_FILE
from researcher import __version__

_ASK_CACHE = {}
_ASK_CACHE_KEYS = []
_ASK_CACHE_MAX = 32
_CLI_LOGGER = None
_LAST_PATH = ""
_LAST_LISTING = []
_MEMORY_DIRTY = False
_OUTPUT_DIR = Path("logs") / "outputs"


def _format_output_for_display(output: str, max_chars: int = 4000) -> str:
    if not output:
        return ""
    if len(output) <= max_chars:
        return output
    lines = output.splitlines()
    summary = f"[output summary: {len(lines)} lines, {len(output)} chars]"
    head = output[:2000].rstrip()
    tail = output[-2000:].lstrip()
    return summary + "\n" + head + "\n...\n[output truncated]\n...\n" + tail


def _store_long_output(output: str, label: str) -> str:
    if not output or len(output) <= 4000:
        return ""
    try:
        from researcher.file_utils import preview_write
        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
        path = _OUTPUT_DIR / f"{ts}_{label}.log"
        if preview_write(path, output):
            path.write_text(output, encoding="utf-8")
        return str(path)
    except Exception:
        return ""


def _write_crash_log(exc: BaseException) -> str:
    if _privacy_enabled_state():
        return ""
    try:
        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
        path = _OUTPUT_DIR / f"{ts}_crash.log"
        payload = [
            "martin crash report",
            f"time_utc: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
            f"cwd: {Path.cwd()}",
            f"argv: {sys.argv}",
            f"python: {sys.version}",
            f"exception: {type(exc).__name__}: {exc}",
            "",
            "traceback:",
            traceback.format_exc(),
            "",
        ]
        path.write_text("\n".join(payload), encoding="utf-8")
        return str(path)
    except Exception:
        return ""


def _privacy_enabled_state() -> bool:
    try:
        st = load_state()
        return st.get("session_privacy") == "no-log"
    except Exception:
        return False


def _logging_verbose(cfg: Optional[Dict[str, Any]] = None) -> bool:
    if _privacy_enabled_state():
        return False
    try:
        cfg = cfg or load_config()
    except Exception:
        cfg = cfg or {}
    logging_cfg = cfg.get("logging", {}) or {}
    return bool(logging_cfg.get("verbose", False))


def _behavior_cfg(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, bool]:
    try:
        cfg = cfg or load_config()
    except Exception:
        cfg = cfg or {}
    behavior = cfg.get("behavior", {}) or {}
    return {
        "summaries": bool(behavior.get("summaries", False)),
        "followup_resolver": bool(behavior.get("followup_resolver", False)),
        "clarification_policy": bool(behavior.get("clarification_policy", False)),
        "context_block": bool(behavior.get("context_block", False)),
    }


def _ui_cfg(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, bool]:
    try:
        cfg = cfg or load_config()
    except Exception:
        cfg = cfg or {}
    ui = cfg.get("ui", {}) or {}
    return {
        "footer": bool(ui.get("footer", False)),
        "startup_compact": bool(ui.get("startup_compact", False)),
    }


def _summarize_user_input(text: str, max_len: int = 200) -> Tuple[str, bool]:
    if not text:
        return "", False
    sanitized, changed = sanitize.sanitize_prompt(text)
    summary = chat_ui.shorten_output(sanitized, max_len=max_len)
    return summary, changed


def _summarize_text(text: str, max_len: int = 200) -> Tuple[str, bool]:
    if not text:
        return "", False
    sanitized, changed = sanitize.sanitize_prompt(text)
    summary = chat_ui.shorten_output(sanitized, max_len=max_len)
    return summary, changed


def _sanitize_command_list(cmds: List[str]) -> Tuple[List[str], bool]:
    out: List[str] = []
    any_changed = False
    for cmd in cmds:
        sanitized, changed = sanitize.sanitize_prompt(cmd)
        any_changed = any_changed or changed
        out.append(chat_ui.shorten_output(sanitized, max_len=200))
    return out, any_changed


def _is_followup(text: str) -> bool:
    lowered = (text or "").strip().lower()
    if not lowered:
        return False
    if lowered in {"do that", "do it", "continue", "go ahead", "yes", "y", "ok", "okay", "yep", "sure"}:
        return True
    return lowered.startswith(("continue", "go ahead", "do that", "do it"))


def _is_short_followup(text: str) -> bool:
    lowered = (text or "").strip().lower()
    if not lowered:
        return False
    if any(k in lowered for k in ("new goal", "change goal", "reset goal", "stop", "cancel")):
        return False
    if lowered in {"sounds good", "looks good", "all good", "go for it", "go ahead"}:
        return True
    return lowered in {"ok", "okay", "sure", "yes", "y", "yep", "yup", "fine"}


def _ingest_allowlist(cfg: Dict[str, Any]) -> Dict[str, Any]:
    ingest_cfg = cfg.get("ingest", {}) or {}
    roots = [Path(r).resolve() for r in (ingest_cfg.get("allowlist_roots") or []) if r]
    exts = [e.lower().lstrip(".") for e in (ingest_cfg.get("allowlist_exts") or []) if e]
    mode = (ingest_cfg.get("allowlist_mode") or "warn").lower()
    return {"roots": roots, "exts": exts, "mode": mode}


def _scan_proprietary_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    ingest_cfg = cfg.get("ingest", {}) or {}
    return {
        "enabled": bool(ingest_cfg.get("scan_proprietary", False)),
        "mode": (ingest_cfg.get("scan_mode") or "warn").lower(),
        "max_bytes": int(ingest_cfg.get("scan_max_bytes") or 200000),
    }


def _is_path_allowed(path: Path, allowlist: Dict[str, Any]) -> bool:
    roots = allowlist.get("roots") or []
    exts = allowlist.get("exts") or []
    if roots and not any(path == r or r in path.parents for r in roots):
        return False
    if exts:
        if path.suffix.lower().lstrip(".") not in exts:
            return False
    return True


def _scan_text_for_sensitive(text: str) -> Tuple[bool, str]:
    sanitized, changed = sanitize.sanitize_prompt(text or "")
    if changed:
        return True, "redaction_detected"
    return False, ""


def _encryption_policy(cfg: Dict[str, Any], current_host: str) -> Dict[str, Any]:
    trust = cfg.get("trust_policy", {}) or {}
    encrypt_exports = bool(trust.get("encrypt_exports", False))
    encrypt_when_remote = bool(trust.get("encrypt_when_remote", True))
    key_env = trust.get("encryption_key_env", "MARTIN_ENCRYPTION_KEY")
    if encrypt_when_remote and current_host and current_host != "local":
        encrypt_exports = True
    return {"encrypt": encrypt_exports, "key_env": key_env}


def _build_active_context(st: Dict[str, Any]) -> Dict[str, Any]:
    tasks = st.get("tasks", []) if isinstance(st.get("tasks"), list) else []
    next_action = tasks[0].get("text") if tasks else ""
    last_plan = st.get("last_plan", {}) if isinstance(st.get("last_plan"), dict) else {}
    last_cmd = st.get("last_command_summary", {}) if isinstance(st.get("last_command_summary"), dict) else {}
    return {
        "goal": st.get("active_goal", ""),
        "next_action": next_action,
        "last_plan_status": last_plan.get("status", ""),
        "last_plan_steps": len(last_plan.get("steps", []) or []),
        "last_command": last_cmd.get("cmd", ""),
        "last_command_rc": last_cmd.get("rc"),
        "last_action_summary": st.get("last_action_summary", ""),
        "tasks_count": len(tasks),
    }


def _plan_to_tasks(cmds: List[str]) -> List[Dict[str, str]]:
    tasks: List[Dict[str, str]] = []
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for cmd in cmds:
        raw = cmd.strip()
        if raw.lower().startswith("command:"):
            raw = raw.split(":", 1)[1].strip()
        label = raw
        if raw.lower().startswith("martin."):
            label = f"internal: {raw}"
        tasks.append({"text": label, "ts": ts})
    return tasks


def _maybe_set_plan_tasks(st: Dict[str, Any], cmds: List[str]) -> bool:
    tasks = st.get("tasks", []) if isinstance(st.get("tasks"), list) else []
    source = st.get("tasks_source", "")
    if tasks and source != "plan":
        return False
    st["tasks"] = _plan_to_tasks(cmds)
    st["tasks_source"] = "plan"
    st.pop("tasks_prompted", None)
    return True


def _maybe_advance_plan_task(ok: bool) -> None:
    if not ok:
        return
    try:
        st = load_state()
        if st.get("tasks_source") != "plan":
            return
        tasks = st.get("tasks", []) if isinstance(st.get("tasks"), list) else []
        if tasks:
            tasks.pop(0)
            st["tasks"] = tasks
            st.pop("tasks_prompted", None)
            save_state(st)
    except Exception:
        pass


def _maybe_update_goal(st: Dict[str, Any], user_text: str, force: bool = False) -> None:
    if not user_text:
        return
    if user_text.strip().startswith("/"):
        return
    lowered = user_text.strip().lower()
    if "new goal:" in lowered or "goal:" in lowered:
        force = True
    if st.get("active_goal") and not force:
        return
    summary, _ = _summarize_user_input(user_text, max_len=140)
    if summary:
        st["active_goal"] = summary


def _null_logger() -> logging.Logger:
    logger = logging.getLogger("martin.cli.noop")
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
    logger.propagate = False
    logger.disabled = True
    return logger


def _get_cli_logger(cfg):
    global _CLI_LOGGER
    if _privacy_enabled_state():
        return _null_logger()
    if _CLI_LOGGER:
        return _CLI_LOGGER
    logs_dir = Path(cfg.get("data_paths", {}).get("logs", "logs"))
    _CLI_LOGGER = setup_logger(logs_dir / "martin.log", name="martin.cli")
    return _CLI_LOGGER

def get_status_payload(cfg, force_simple: bool = False) -> Dict[str, Any]:
    import time
    from researcher.llm_utils import MODEL_MAIN
    t0 = time.perf_counter()
    idx = _load_index(cfg, force_simple=force_simple)
    load_ms = (time.perf_counter() - t0) * 1000.0
    vs = cfg.get("vector_store", {}) or {}
    st = load_state()
    local_llm_cfg = cfg.get("local_llm", {}) or {}
    local_enabled = bool(local_llm_cfg.get("enabled", cfg.get("local_llm_enabled", False)))
    local_stream = bool(local_llm_cfg.get("streaming", False))
    fallbacks = local_llm_cfg.get("fallbacks", []) or []
    health = check_ollama_health(cfg.get("ollama_host", "http://localhost:11434"), cfg.get("local_model", "phi3"))
    remote_status = {}
    try:
        remote_status = status_tunnel(cfg)
    except Exception:
        remote_status = {}
    return {
        "version": __version__,
        "model_main": MODEL_MAIN,
        "local_model": str(cfg.get("local_model")),
        "local_model_ok": bool(health.get("ok")),
        "local_llm_enabled": local_enabled,
        "local_llm_streaming": local_stream,
        "local_llm_fallbacks": fallbacks,
        "embedding_model": str(cfg.get("embedding_model")),
        "index_type": vs.get("type", "simple"),
        "index_path": str(vs.get("index_path", "")),
        "index_docs": idx.stats().get("count"),
        "index_load_ms": round(load_ms, 2),
        "state": {
            "session_count": st.get("session_count"),
            "last_session_start": st.get("last_session", {}).get("started_at"),
            "ledger_entries": st.get("ledger", {}).get("entries"),
            "workspace_path": st.get("workspace", {}).get("path"),
            "current_host": st.get("current_host", ""),
        },
        "remote_transport": remote_status,
        "local_only": bool(cfg.get("local_only")),
    }

def should_cloud_hop(cloud_mode: str, top_score: float, threshold: float) -> bool:
    if cloud_mode == "always":
        return True
    if cloud_mode == "auto":
        return top_score < (threshold or 0.0)
    return False


def read_prompt(args: argparse.Namespace) -> str:
    if args.stdin:
        return sys.stdin.read().strip()
    return " ".join(args.prompt or []).strip()


def _load_index(cfg, force_simple: bool = False):
    vs = cfg.get("vector_store", {}) or {}
    index_path = Path(vs.get("index_path", "data/index/mock_index.pkl"))
    mock_path = Path(vs.get("mock_index_path", "data/index/mock_index.pkl"))
    idx_type = vs.get("type", "simple")
    if force_simple:
        return SimpleIndex.load(mock_path)
    if idx_type == "faiss":
        model = cfg.get("embedding_model", "all-MiniLM-L6-v2")
        try:
            idx = FaissIndex.load(model_name=model, index_path=index_path)
            # probe model availability early
            idx._ensure_model()
            return idx
        except Exception as e:
            print(f"[warn] FAISS/embedding load failed ({e}); falling back to SimpleIndex {mock_path}", file=sys.stderr)
            return SimpleIndex.load(mock_path)
    return SimpleIndex.load(mock_path)


def cmd_status(cfg, force_simple: bool = False, as_json: bool = False) -> int:
    from rich.console import Console
    from rich.table import Table
    payload = get_status_payload(cfg, force_simple=force_simple)
    _get_cli_logger(cfg).info("status force_simple=%s as_json=%s", force_simple, as_json)
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    console = Console()
    table = Table(title="Status")
    table.add_column("field", style="cyan")
    table.add_column("value", style="white")
    rows = [
        ("version", payload.get("version")),
        ("local_model", payload.get("local_model")),
        ("local_model_ok", str(payload.get("local_model_ok"))),
        ("local_llm_enabled", str(payload.get("local_llm_enabled"))),
        ("local_llm_streaming", str(payload.get("local_llm_streaming"))),
        ("local_llm_fallbacks", ", ".join(payload.get("local_llm_fallbacks", []) or [])),
        ("embedding_model", payload.get("embedding_model")),
        ("index_type", payload.get("index_type")),
        ("index_path", payload.get("index_path")),
        ("index_docs", str(payload.get("index_docs"))),
        ("index_load_ms", f"{payload.get('index_load_ms', 0):.2f}"),
    ]
    remote = payload.get("remote_transport", {}) or {}
    if remote:
        rows.append(("remote_transport", remote.get("status")))
    for k, v in rows:
        table.add_row(k, v)
    console.print(table)
    # Add researcher state info
    state_table = Table(title="Researcher State")
    state_table.add_column("field", style="cyan")
    state_table.add_column("value", style="white")
    state = payload.get("state", {}) or {}
    state_table.add_row("session_count", str(state.get("session_count")))
    state_table.add_row("last_session_start", str(state.get("last_session_start")))
    state_table.add_row("ledger_entries", str(state.get("ledger_entries")))
    state_table.add_row("workspace_path", str(state.get("workspace_path")))
    state_table.add_row("local_only", str(payload.get("local_only")))
    console.print(state_table)
    return 0


def _collect_ingest_files(inputs: List[str], exts: Optional[List[str]] = None, max_files: int = 0) -> List[str]:
    import glob
    files = []
    seen = set()
    exts_norm = [e.lower().lstrip(".") for e in (exts or []) if e]
    for item in inputs:
        if "*" in item or "?" in item:
            matches = glob.glob(item, recursive=True)
            for m in matches:
                p = Path(m)
                if p.is_file():
                    files.append(str(p))
        else:
            p = Path(item)
            if p.is_dir():
                for f in p.rglob("*"):
                    if f.is_file():
                        files.append(str(f))
            elif p.is_file():
                files.append(str(p))
    out = []
    for f in files:
        if max_files and len(out) >= max_files:
            break
        p = Path(f)
        if exts_norm:
            if p.suffix.lower().lstrip(".") not in exts_norm:
                continue
        if f in seen:
            continue
        seen.add(f)
        out.append(f)
    return out


def _extract_paths_from_text(text: str) -> List[str]:
    candidates: List[str] = []
    try:
        candidates.extend(shlex.split(text))
    except Exception:
        candidates.extend(text.split())
    candidates.extend(re.findall(r"[A-Za-z]:\\[^\s\"']+|/[^\s\"']+", text))
    seen = set()
    out: List[str] = []
    for raw in candidates:
        val = raw.strip().strip("\"'")
        val = val.rstrip(").,;:!?]")
        if not val or val in seen:
            continue
        p = Path(val)
        if p.exists():
            seen.add(val)
            out.append(val)
    return out


def _extract_desktop_targets(text: str) -> List[str]:
    lowered = text.lower()
    if "desktop" not in lowered:
        return []
    try:
        ctx = get_system_context()
    except NameError:
        try:
            from researcher.system_context import get_system_context as _get_system_context
            ctx = _get_system_context()
        except Exception:
            return []
    base = ""
    if "onedrive" in lowered and ctx.get("paths", {}).get("onedrive_desktop"):
        base = ctx["paths"]["onedrive_desktop"]
    elif ctx.get("paths", {}).get("desktop"):
        base = ctx["paths"]["desktop"]
    if not base:
        return []
    matches = re.findall(r"([A-Za-z0-9_\- .]+\\.[A-Za-z0-9]{1,5})", text)
    out: List[str] = []
    for name in matches:
        candidate = str(Path(base) / name.strip())
        if Path(candidate).exists():
            out.append(candidate)
    return out


def _build_librarian_ingest_note(paths: List[str], max_files: int = 5, max_chars: int = 4000) -> str:
    if not paths:
        return ""
    parts = [f"Ingested {len(paths)} files into local RAG. Redacted previews:"]
    for p in paths[:max_files]:
        name = Path(p).name
        try:
            text = Path(p).read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            parts.append(f"- {name}: [read error: {e}]")
            continue
        if len(text) > max_chars:
            text = text[:max_chars] + "\n...[truncated]"
        sanitized, _ = sanitize.sanitize_prompt(text)
        preview = " ".join((sanitized or "").split())
        if len(preview) > 400:
            preview = preview[:400] + "…"
        parts.append(f"- {name}: {preview}")
    if len(paths) > max_files:
        parts.append(f"... {len(paths) - max_files} more files omitted")
    return "\n".join(parts)


def _confirm_cloud_send(prompt: str, approval_policy: str, agent_mode: bool = False, as_json: bool = False) -> Tuple[bool, str]:
    sanitized, _changed = sanitize.sanitize_prompt(prompt or "")
    if approval_policy == "never" or agent_mode:
        return True, sanitized
    if as_json:
        return False, sanitized
    print("\033[96mmartin: Cloud preview (sanitized)\033[0m")
    print(sanitized)
    try:
        resp = input("Send to cloud? (yes/no) ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        resp = "no"
    return resp in {"y", "yes"}, sanitized


def cmd_ingest(cfg, paths: List[str], force_simple: bool = False, exts: Optional[List[str]] = None, max_files: int = 0, as_json: bool = False, skip_librarian: bool = False) -> int:
    if not paths:
        print("No files provided to ingest.", file=sys.stderr)
        return 1
    local_only = bool(cfg.get("local_only")) or os.environ.get("RESEARCHER_LOCAL_ONLY", "").strip().lower() in {"1", "true", "yes"}
    if local_only:
        skip_librarian = True
    ensure_dirs(cfg)
    st = load_state()
    _get_cli_logger(cfg).info("ingest paths=%d force_simple=%s max_files=%d", len(paths), force_simple, max_files)
    expanded = _collect_ingest_files(paths, exts=exts, max_files=max_files)
    existing_paths = [p for p in expanded if Path(p).exists()]
    allowlist = _ingest_allowlist(cfg)
    scan_cfg = _scan_proprietary_cfg(cfg)
    blocked_paths = []
    allowed_paths = []
    for p in existing_paths:
        path = Path(p)
        if not _is_path_allowed(path, allowlist):
            blocked_paths.append(p)
            continue
        allowed_paths.append(p)
    if blocked_paths:
        log_event(st, "ingest_allowlist_blocked", blocked_count=len(blocked_paths), blocked=blocked_paths[:10])
        if allowlist.get("mode") == "block":
            msg = "Ingest blocked by allowlist."
            if as_json:
                print(json.dumps({"ok": False, "error": "allowlist_blocked", "blocked": blocked_paths[:10]}, ensure_ascii=False))
            else:
                print(msg, file=sys.stderr)
            return 1
    if not allowed_paths:
        msg = "No valid files found to ingest."
        log_event(st, "ingest_command_failed", files_count=0, error="no_valid_files")
        if as_json:
            print(json.dumps({"ok": False, "error": "no_valid_files"}, ensure_ascii=False))
        else:
            print(msg, file=sys.stderr)
        return 1

    trust_policy = cfg.get("trust_policy", {}) or {}
    trust_label = trust_policy.get("default_source", "internal")
    idx = _load_index(cfg, force_simple=force_simple)
    files = [Path(p) for p in allowed_paths]
    scan_hits = []
    if scan_cfg.get("enabled"):
        for fp in files:
            try:
                data = fp.read_bytes()[: scan_cfg.get("max_bytes", 200000)]
                text = data.decode("utf-8", errors="ignore")
            except Exception:
                continue
            flagged, reason = _scan_text_for_sensitive(text)
            if flagged:
                scan_hits.append({"path": str(fp), "reason": reason})
        if scan_hits:
            log_event(st, "ingest_scan_flagged", hits=len(scan_hits), samples=scan_hits[:5])
            if scan_cfg.get("mode") == "block":
                msg = "Ingest blocked by proprietary scan."
                if as_json:
                    print(json.dumps({"ok": False, "error": "scan_blocked", "hits": scan_hits[:5]}, ensure_ascii=False))
                else:
                    print(msg, file=sys.stderr)
                return 1
    local_result = ingest_files(idx, files, trust_label=trust_label, source_type="local")
    save_index_from_config(cfg, idx)
    log_event(st, "ingest_command", files_count=len(files), errors_count=len(local_result.get("errors", [])), idx_type="local")
    try:
        st = load_state()
        st["last_ingest"] = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "count": local_result.get("ingested", 0), "mode": "local"}
        save_state(st)
    except Exception:
        pass
    if not skip_librarian:
        try:
            st = load_state()
            if st.get("session_privacy") != "no-log":
                note = _build_librarian_ingest_note(existing_paths)
                if note:
                    client = LibrarianClient()
                    client.ingest_text(note, topic="local_ingest_notice", source="local_ingest_redacted")
                    client.close()
        except Exception:
            pass
    if as_json:
        print(json.dumps({"ok": True, "mode": "local", "ingested": local_result.get("ingested", 0), "errors": local_result.get("errors", [])}, ensure_ascii=False))
    else:
        for err in local_result.get("errors", []):
            print(f"error: {err}", file=sys.stderr)
        print(f"Ingested {local_result.get('ingested', 0)} files (local)")
    return 0


def cmd_ask(cfg, prompt: str, k: int, use_llm: bool = False, cloud_mode: str = "off", cloud_cmd: str = "", cloud_threshold: float = None, force_simple: bool = False, as_json: bool = False) -> int:
    from rich.console import Console
    from rich.table import Table
    from researcher.cloud_bridge import _hash
    ensure_dirs(cfg)
    st = load_state() # Load state for logging
    _get_cli_logger(cfg).info("ask prompt_len=%d k=%d use_llm=%s cloud_mode=%s force_simple=%s as_json=%s", len(prompt or ""), k, use_llm, cloud_mode, force_simple, as_json)
    if not (prompt or "").strip():
        if as_json:
            print(json.dumps({"ok": False, "error": "empty_prompt"}, ensure_ascii=False))
        else:
            print("No prompt provided (use args or --stdin).", file=sys.stderr)
        log_event(st, "ask_command_failed", error="empty_prompt")
        return 1
    # logger = setup_logger(Path(cfg.get("data_paths", {}).get("logs", "logs")) / "local.log") # No longer needed directly here
    vs = cfg.get("vector_store", {}) or {}
    idx_path = Path(vs.get("index_path", "data/index/mock_index.pkl"))
    idx = _load_index(cfg, force_simple=force_simple)
    sanitized, changed = sanitize.sanitize_prompt(prompt)
    # Simple memo cache (in-process)
    cache_key = _hash(sanitized)
    cached = _ASK_CACHE.get(cache_key)
    if cached:
        answer = cached.get("answer", "")
        hits = cached.get("hits", [])
        cloud_hits = cached.get("cloud_hits", [])
        log_event(st, "ask_cache_hit", key=cache_key)
        if as_json:
            print(json.dumps({
                "ok": True,
                "cached": True,
                "answer": answer,
                "top_score": None,
                "hits": hits,
                "cloud_hits": cloud_hits,
                "sanitized": changed,
            }, ensure_ascii=False))
            return 0
        resp = build_response("cli", answer=answer, hits=hits, logs_ref=str(idx_path), cloud_hits=cloud_hits)
        console = Console()
        print(f"[cache] confidence: cached | hits: {len(hits)} | cloud: {len(cloud_hits)}")
        print("\nAnswer:\n" + answer + "\n")
        table = Table(title="Local Results (cached)")
        table.add_column("score", style="cyan")
        table.add_column("source", style="magenta")
        table.add_column("chunk", style="white")
        for entry in resp.provenance.get("local", []):
            table.add_row(f"{entry.score:.3f}", entry.source, entry.text[:80])
        console.print(table)
        if resp.provenance.get("cloud"):
            cloud_table = Table(title="Cloud Results (cached)")
            cloud_table.add_column("score", style="cyan")
            cloud_table.add_column("source", style="magenta")
            cloud_table.add_column("chunk", style="white")
            for entry in resp.provenance["cloud"]:
                cloud_table.add_row(f"{entry.score:.3f}", entry.source, entry.text[:80])
            console.print(cloud_table)
        if changed:
            print("[sanitized input used]", file=sys.stderr)
        return 0

    hits = idx.search(sanitized, k=k)
    trust_policy = cfg.get("trust_policy", {}) or {}
    allowed_sources = trust_policy.get("allow_sources") or []
    if allowed_sources:
        filtered = []
        for score, meta in hits:
            trust = (meta.get("trust") or "internal").lower()
            if trust in [s.lower() for s in allowed_sources]:
                filtered.append((score, meta))
        if len(filtered) != len(hits):
            log_event(st, "rag_trust_filter", before=len(hits), after=len(filtered))
        hits = filtered
    top_score = max([h[0] for h in hits], default=0.0)
    log_event(st, "ask_command", k=k, hits_count=len(hits), top_score=top_score, sanitized=changed) # Use state_manager's log_event
    gap_threshold = cfg.get("auto_update", {}).get("ingest_threshold", 0.1)
    if top_score < gap_threshold:
        sanitized_prompt, changed_gap = sanitize.sanitize_prompt(prompt or "")
        log_event(st, "rag_gap", top_score=top_score, prompt=sanitized_prompt, sanitized=changed_gap)
    answer = compose_answer(hits)
    cloud_hits = []
    # Variable to track if a cloud answer was suggested for ingestion
    cloud_answer_ingested = False
    # Optional local LLM generation
    llm_answer = None
    local_llm_cfg = cfg.get("local_llm", {}) or {}
    local_enabled = bool(local_llm_cfg.get("enabled", cfg.get("local_llm_enabled", False)))
    local_stream = bool(local_llm_cfg.get("streaming", False))
    fallbacks = local_llm_cfg.get("fallbacks", []) or []
    streamed = False
    if local_enabled or use_llm:
        ctx = "\n".join([meta.get("chunk", "") for _, meta in hits][:3])
        llm_prompt = f"Context:\n{ctx}\n\nUser question:\n{prompt}\n\nAnswer concisely. If no context, say so."
        model = cfg.get("local_model", "phi3")
        if local_stream:
            def _stream_token(tok: str) -> None:
                print(tok, end="", flush=True)
            print("Answer (streaming):")
            llm_answer = run_ollama_chat_stream(model, llm_prompt, cfg.get("ollama_host", "http://localhost:11434"), on_token=_stream_token)
            print("")
            streamed = True
        else:
            llm_answer = run_ollama_chat(model, llm_prompt, cfg.get("ollama_host", "http://localhost:11434"))
        if not llm_answer and fallbacks:
            for fb in fallbacks:
                if not fb:
                    continue
                llm_answer = run_ollama_chat(fb, llm_prompt, cfg.get("ollama_host", "http://localhost:11434"))
                if llm_answer:
                    log_event(st, "ask_local_llm_fallback", model=fb)
                    break
        log_event(st, "ask_local_llm", llm_used=bool(llm_answer), streamed=streamed)
        if llm_answer:
            answer = llm_answer

    # --- Auto-update trigger: Low confidence local retrieval ---
    # Retrieve auto_ingest_threshold from config, default to 0.1
    auto_ingest_threshold = cfg.get("auto_update", {}).get("ingest_threshold", 0.1)
    if top_score < auto_ingest_threshold:
        log_event(st, "low_confidence_retrieval", top_score=top_score, threshold=auto_ingest_threshold, prompt=prompt)
        print(f"\033[93mmartin: Low confidence local retrieval (score: {top_score:.2f}). Consider ingesting more relevant documents for '{prompt}'.\033[0m", file=sys.stderr)

    # Optional cloud hop
    cloud_cfg = cfg.get("cloud", {}) or {}
    local_only = bool(cfg.get("local_only")) or os.environ.get("RESEARCHER_LOCAL_ONLY", "").strip().lower() in {"1", "true", "yes"}
    effective_cloud_cmd = cloud_cmd or cloud_cfg.get("cmd_template") or os.environ.get("CLOUD_CMD", "")
    threshold = cloud_threshold if cloud_threshold is not None else cloud_cfg.get("trigger_score", 0.0)
    should_cloud = should_cloud_hop(cloud_mode, top_score, threshold)
    if should_cloud and not local_only:
        from researcher.cloud_bridge import _hash
        exec_cfg = cfg.get("execution", {}) or {}
        approval_policy = (exec_cfg.get("approval_policy") or "on-request").lower()
        client = LibrarianClient()
        allow_cloud, sanitized_prompt = _confirm_cloud_send(prompt or "", approval_policy, agent_mode=False, as_json=as_json)
        if allow_cloud:
            cloud_resp = client.query_cloud(
                prompt=sanitized_prompt,
                cloud_mode=cloud_mode,
                cloud_cmd=effective_cloud_cmd,
                cloud_threshold=cloud_threshold # Pass for context, though Librarian handles thresholding
            )
        else:
            cloud_resp = {"status": "error", "message": "user_denied"}
        client.close() # Close connection after use

        # Adapt cloud_resp from Librarian to CloudCallResult format for existing logic
        if cloud_resp.get("status") == "success":
            result_data = cloud_resp.get("result", {})
            # Assuming Librarian's result matches CloudCallResult structure
            result_ok = result_data.get("ok", False)
            result_output = result_data.get("output", "")
            result_error = result_data.get("error", "")
            result_rc = result_data.get("rc", 1)
            result_sanitized = result_data.get("sanitized", "")
            result_changed = result_data.get("changed", False)
            result_hash = result_data.get("hash", "")
        else:
            # Handle error from Librarian client or Librarian itself
            result_ok = False
            result_output = ""
            result_error = cloud_resp.get("message", "Error communicating with Librarian")
            result_rc = 1
            result_sanitized = sanitized
            result_changed = changed
            result_hash = _hash(sanitized) # Re-hash for logging consistency if error

        log_event(st, "ask_cloud_hop", cloud_mode=cloud_mode, rc=result_rc, redacted=(result_changed or False), trigger_score=top_score, threshold=threshold, librarian_response_status=cloud_resp.get("status")) # Use state_manager's log_event
        if result_ok and result_output:
            cloud_hits.append((0.0, {"path": "cloud", "chunk": result_output}))
            # --- Auto-update trigger: Ingest successful cloud answer ---
            if cfg.get("auto_update", {}).get("ingest_cloud_answers", False):
                from researcher.ingester import simple_chunk
                chunks = simple_chunk(result_output)
                if chunks:
                    if isinstance(idx, FaissIndex):
                        metas = [{"path": "cloud", "chunk": c[:200], "provenance": "cloud"} for c in chunks]
                        idx.add(chunks, metas)
                    else:
                        for c in chunks:
                            idx.add(c, {"path": "cloud", "chunk": c[:200], "provenance": "cloud"})
                    if hasattr(idx, "save"):
                        idx.save()
                    log_event(st, "ingest_cloud_answer", chunks=len(chunks), cloud_output_hash=_hash(result_output), prompt=prompt)
                    print(f"\033[92mmartin: Cloud answer ingested into local RAG ({len(chunks)} chunks).\033[0m", file=sys.stderr)
                    cloud_answer_ingested = True # Set flag for response building
                else:
                    log_event(st, "ingest_cloud_answer_skipped", reason="empty_chunks", cloud_output_hash=_hash(result_output))
        elif result_error:
            print(f"[cloud] {result_error}", file=sys.stderr)
    elif cloud_mode != "off" and local_only:
        log_event(st, "ask_cloud_hop_blocked", reason="local_only", cloud_mode=cloud_mode)
    elif cloud_mode != "off":
        from researcher.cloud_bridge import _hash
        log_event(st, "ask_cloud_hop_skipped", cloud_mode=cloud_mode, skipped_reason="threshold", trigger_score=top_score, threshold=threshold)

    # For now, just pass cloud_answer_ingested status through logs_ref or similar if needed.
    # The actual ingestion of the cloud answer would be a separate, more complex step. # Use state_manager's log_event

    resp = build_response("cli", answer=answer, hits=hits, logs_ref=str(idx_path), cloud_hits=cloud_hits)
    console = Console()
    if as_json:
        print(json.dumps({
            "ok": True,
            "cached": False,
            "answer": answer,
            "top_score": top_score,
            "hits": hits,
            "cloud_hits": cloud_hits,
            "sanitized": changed,
        }, ensure_ascii=False))
        _ASK_CACHE[cache_key] = {"answer": answer, "hits": hits, "cloud_hits": cloud_hits}
        _ASK_CACHE_KEYS.append(cache_key)
        if len(_ASK_CACHE_KEYS) > _ASK_CACHE_MAX:
            old = _ASK_CACHE_KEYS.pop(0)
            _ASK_CACHE.pop(old, None)
        log_event(st, "ask_cache_put", key=cache_key)
        return 0
    print(f"confidence: {top_score:.3f} | hits: {len(hits)} | cloud: {len(cloud_hits)}")
    if not streamed:
        print("\nAnswer:\n" + answer + "\n")
    table = Table(title="Local Results")
    table.add_column("score", style="cyan")
    table.add_column("source", style="magenta")
    table.add_column("chunk", style="white")
    for entry in resp.provenance.get("local", []):
        table.add_row(f"{entry.score:.3f}", entry.source, entry.text[:80])
    console.print(table)
    if resp.provenance.get("cloud"):
        cloud_table = Table(title="Cloud Results")
        cloud_table.add_column("score", style="cyan")
        cloud_table.add_column("source", style="magenta")
        cloud_table.add_column("chunk", style="white")
        for entry in resp.provenance["cloud"]:
            cloud_table.add_row(f"{entry.score:.3f}", entry.source, entry.text[:80])
        console.print(cloud_table)
    if changed:
        print("[sanitized input used]", file=sys.stderr)
    _ASK_CACHE[cache_key] = {"answer": answer, "hits": hits, "cloud_hits": cloud_hits}
    _ASK_CACHE_KEYS.append(cache_key)
    if len(_ASK_CACHE_KEYS) > _ASK_CACHE_MAX:
        old = _ASK_CACHE_KEYS.pop(0)
        _ASK_CACHE.pop(old, None)
    log_event(st, "ask_cache_put", key=cache_key)
    return 0

# New cmd_chat function
def cmd_chat(cfg, args) -> int:
    from tqdm import tqdm
    from researcher.command_utils import extract_commands, classify_command_risk, edit_commands_in_editor, edit_commands_inline
    from researcher.llm_utils import _post_responses, _extract_output_text, MODEL_MAIN, MODEL_MINI, interaction_history, diagnose_failure, current_username, rephraser
    from researcher.orchestrator import decide_next_step, dispatch_internal_ability
    from researcher.resource_registry import list_resources, read_resource
    from researcher.runner import run_command_smart_capture, enforce_sandbox
    from researcher.librarian_client import LibrarianClient
    from researcher.system_context import get_system_context
    from researcher.tool_ledger import append_tool_entry, read_recent, export_json, build_export_json
    from researcher.file_utils import preview_write
    from researcher.worklog import append_worklog, read_worklog
    from researcher.logbook_utils import append_logbook_entry
    import shlex
    import subprocess
    ui_flags = _ui_cfg(cfg)
    compact_startup = bool(ui_flags.get("startup_compact", False))

    def _handle_librarian_notification(message: Dict[str, Any]) -> None:
        try:
            trust_policy = cfg.get("trust_policy", {}) or {}
            if not trust_policy.get("allow_librarian_notes", True):
                log_event(load_state(), "librarian_note_blocked", reason="trust_policy")
                return
            try:
                details = message.get("details", {}) if isinstance(message, dict) else {}
                err = (details.get("error") or "").lower()
                if "local-only" in err or "local_only" in err:
                    print("\033[93mmartin: Librarian note blocked by local-only mode.\033[0m")
                    log_event(load_state(), "librarian_note_blocked", reason="local_only")
            except Exception:
                pass
            st = load_state()
            inbox = st.get("librarian_inbox", [])
            if not isinstance(inbox, list):
                inbox = []
            max_items = int(os.environ.get("LIBRARIAN_INBOX_MAX", "50"))
            retention_days = int(os.environ.get("LIBRARIAN_INBOX_RETENTION_DAYS", "14"))
            cutoff = None
            if retention_days > 0:
                try:
                    cutoff = time.time() - (retention_days * 86400)
                except Exception:
                    cutoff = None
            inbox.append({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "message": message,
            })
            if cutoff:
                pruned = []
                for item in inbox:
                    ts = item.get("ts", "")
                    try:
                        dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        if dt.timestamp() >= cutoff:
                            pruned.append(item)
                    except Exception:
                        pruned.append(item)
                inbox = pruned
            st["librarian_inbox"] = inbox[-max_items:]
            st["librarian_unread"] = True
            save_state(st)
        except Exception:
            pass

    def _context_delta(prev: Dict[str, Any], curr: Dict[str, Any]) -> Dict[str, Any]:
        delta: Dict[str, Any] = {}
        try:
            prev_recent = set(prev.get("recent_files", []) or [])
            curr_recent = set(curr.get("recent_files", []) or [])
            if curr_recent - prev_recent:
                delta["new_recent_files"] = sorted(list(curr_recent - prev_recent))[:20]
        except Exception:
            pass
        try:
            prev_git = (prev.get("git_status") or "").splitlines()
            curr_git = (curr.get("git_status") or "").splitlines()
            if prev_git or curr_git:
                delta["git_status"] = (curr_git[:1][0] if curr_git else "")
        except Exception:
            pass
        return delta

    def _safe_json(text: str) -> Dict[str, Any]:
        if not text:
            return {}
        try:
            return json.loads(text)
        except Exception:
            pass
        m = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not m:
            return {}
        try:
            return json.loads(m.group(0))
        except Exception:
            return {}

    def _plan_action_queue(prompt: str, ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
        sanitized, _changed = sanitize.sanitize_prompt(prompt or "")
        ctx_summary = {
            "root": ctx.get("root"),
            "git": (ctx.get("git_status") or "").splitlines()[:1],
            "recent_files": ctx.get("recent_files", [])[:5],
        }
        planner_sys = (
            "You are a concise planning assistant. "
            "Return JSON only with keys: queue, checkins, assumptions. "
            "queue is a list of 3-7 ordered steps with fields: title, action, command, blocking, success. "
            "Use command only when a shell command is needed; otherwise empty string. "
            "Keep actions short and practical."
        )
        planner_user = (
            f"User request:\n{sanitized}\n\n"
            f"Context:\n{json.dumps(ctx_summary, ensure_ascii=False)}\n\n"
            "Return JSON only."
        )
        payload = {
            "model": MODEL_MINI,
            "input": [
                {"role": "system", "content": planner_sys},
                {"role": "user", "content": planner_user},
            ],
            "temperature": 0.2,
            "max_output_tokens": 600,
        }
        resp = _post_responses(payload, label="Planner")
        text = _extract_output_text(resp) or ""
        data = _safe_json(text)
        queue = data.get("queue")
        return queue if isinstance(queue, list) else []

    def _render_action_queue(queue: List[Dict[str, Any]]) -> None:
        if not queue:
            print("martin: Action queue is empty.")
            return
        print("martin: Action queue")
        for idx, item in enumerate(queue, 1):
            title = (item.get("title") or "").strip() or "step"
            action = (item.get("action") or "").strip()
            command = (item.get("command") or "").strip()
            blocking = item.get("blocking")
            line = f"{idx}. {title}"
            if action:
                line += f" - {action}"
            if command:
                line += f" | command: {command}"
            if blocking is True:
                line += " | blocking"
            print(line)
        next_title = (queue[0].get("title") or "").strip() if queue else ""
        if next_title:
            print(f"martin: Summary: queued {len(queue)} steps. Next: {next_title}.")

    def _auto_context_surface(reason: str, quiet: bool = False) -> None:
        nonlocal context_cache
        try:
            st = load_state()
            prev = st.get("context_cache", {}) if isinstance(st, dict) else {}
            from researcher.context_harvest import gather_context
            fast_ctx = not (Path.cwd() / ".git").exists()
            context_cache = gather_context(Path.cwd(), max_recent=int(cfg.get("context", {}).get("max_recent", 10)), fast=fast_ctx)
            st = load_state()
            st["context_cache"] = context_cache
            save_state(st)
            delta = _context_delta(prev if isinstance(prev, dict) else {}, context_cache)
            if not quiet:
                print(f"\033[96mmartin: Context update ({reason})\033[0m")
                chat_ui.print_context_summary(context_cache)
                if delta:
                    parts = []
                    new_files = delta.get("new_recent_files") or []
                    if new_files:
                        parts.append(f"new files: {len(new_files)}")
                    git_status = delta.get("git_status")
                    if git_status:
                        parts.append(f"git: {git_status}")
                    if parts:
                        print("martin: Context changes - " + " | ".join(parts))
        except Exception:
            pass
    def _run_onboarding() -> None:
        st = load_state()
        if st.get("onboarding_complete"):
            print("martin: Onboarding already completed. Use /onboarding to re-run.")
        print("\033[96mmartin: Onboarding checklist\033[0m")
        print("- Verify local-only mode (`/status` shows local_only true if desired).")
        print("- Run `/verify` to check venv/scripts/remote config.")
        print("- Set your logbook handle (first clock-in prompt).")
        print("- Run tests: `/tests` then `/tests run <n>`.")
        print("- Review tickets in docs/tickets.md.")
        print("- Install launcher if desired (scripts/install_martin.ps1).")
        try:
            confirm = input("Mark onboarding complete? (yes/no) ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            confirm = "no"
        if confirm == "yes":
            st["onboarding_complete"] = True
            st["onboarding_ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            save_state(st)
            print("martin: Onboarding marked complete.")
    def _mo_preflight_check() -> None:
        root = Path.cwd()
        checks = []
        git_status = ""
        git_ok = (root / ".git").exists()
        if git_ok:
            try:
                res = subprocess.run(["git", "status", "-sb"], capture_output=True, text=True, check=False)
                git_status = (res.stdout or res.stderr or "").splitlines()[:1]
                git_status = git_status[0] if git_status else ""
            except Exception:
                git_status = "git status unavailable"
        checks.append(("git", git_status or "no git repo"))
        checks.append(("tickets", "ok" if (root / "docs" / "tickets.md").exists() else "missing"))
        checks.append(("bug_log", "ok" if (root / "docs" / "bug_log.md").exists() else "missing"))
        checks.append(("logbook", "ok" if (root / "docs" / "logbook.md").exists() else "missing"))
        st = load_state()
        last_test = st.get("tests_last", {}) if isinstance(st, dict) else {}
        if last_test:
            checks.append(("last_test", f"{'ok' if last_test.get('ok') else 'fail'} {last_test.get('ts','')}"))
        else:
            checks.append(("last_test", "none (run /tests)"))
        if compact_startup:
            summary = " | ".join(f"{key}={val}" for key, val in checks)
            print(f"martin: Preflight {summary}")
        else:
            print("\033[96mmartin: Preflight checks\033[0m")
            for key, val in checks:
                print(f"- {key}: {val}")
        missing = [c for c in checks if c[1] in ("missing", "none (run /tests)")]
        if missing:
            if compact_startup:
                missing_list = ", ".join(name for name, _ in missing)
                print(f"martin: Missing: {missing_list}")
            else:
                print("martin: Next steps: address missing items before heavy changes.")
        if not compact_startup:
            print("martin: Quickstart: /verify, /tests, docs/tickets.md, /help")
            print("martin: Logs: logs/martin.log, logs/researcher_ledger.ndjson")
        append_worklog("plan", "preflight checks complete")

    def _startup_progress(step: int, total: int, label: str, status: str) -> None:
        bar_len = 10
        filled = int((step / total) * bar_len)
        bar = "#" * filled + "-" * (bar_len - filled)
        print(f"martin: Startup [{bar}] {step}/{total} {label} {status}")

    def _work_status(label: str) -> None:
        if not sys.stdout.isatty():
            print(f"martin: Working: {label}")
            return
        print(f"\rmartin: Working: {label}", end="", flush=True)

    def _work_status_done() -> None:
        if not sys.stdout.isatty():
            return
        print("\r" + (" " * 120) + "\r", end="", flush=True)

    def _work_spinner(label: str, stop_event: threading.Event, label_ref: Optional[Dict[str, str]] = None) -> threading.Thread:
        if not sys.stdout.isatty():
            _work_status(label)
            return threading.Thread()
        def _spin() -> None:
            frames = ["|", "/", "-", "\\"]
            idx = 0
            while not stop_event.is_set():
                current = (label_ref.get("label") if label_ref else None) or label
                print(f"\rmartin: Working: {current} {frames[idx % len(frames)]}", end="", flush=True)
                idx += 1
                time.sleep(0.25)
            _work_status_done()
        t = threading.Thread(target=_spin, daemon=True)
        t.start()
        return t

    def _ensure_handle() -> str:
        st = load_state()
        handle = ""
        if isinstance(st, dict):
            handle = st.get("operator_handle", "") or ""
        if not handle:
            default_handle = current_username or "user"
            try:
                entered = read_user_input(f"martin: Handle for logbook? (enter for {default_handle}) ").strip()
            except (EOFError, KeyboardInterrupt):
                entered = ""
            handle = entered or default_handle
            if isinstance(st, dict):
                st["operator_handle"] = handle
                save_state(st)
        return handle

    def _prompt_clock(action: str) -> None:
        handle = _ensure_handle()
        note = f"auto: {action.lower()}"
        append_logbook_entry(handle, action, note)
        append_worklog("doing", f"{action} recorded (auto)")

    def _run_cmd_with_worklog(cmd: str) -> Tuple[bool, str, str, int]:
        append_worklog("doing", f"run: {cmd}")
        ok, stdout, stderr, rc = run_command_smart_capture(cmd)
        if rc == 130:
            append_worklog("cancel", f"rc=130 {cmd}")
        else:
            append_worklog("done", f"rc={rc} {cmd}")
        return ok, stdout, stderr, rc

    def _run_remote_cmd(cmd: str) -> Tuple[bool, str, str, int]:
        append_worklog("doing", f"remote: {cmd}")
        rt = cfg.get("remote_transport", {}) or {}
        ssh_host = rt.get("ssh_host", "")
        ssh_user = rt.get("ssh_user", "")
        identity_file = rt.get("identity_file", "")
        if not ssh_host:
            return False, "", "remote transport missing ssh_host", 2
        user_host = f"{ssh_user}@{ssh_host}" if ssh_user else ssh_host
        args = ["ssh", user_host, cmd]
        if identity_file:
            args = ["ssh", "-i", identity_file, user_host, cmd]
        try:
            res = subprocess.run(args, capture_output=True, text=True, check=False)
            stdout = res.stdout or ""
            stderr = res.stderr or ""
            rc = res.returncode
            ok = rc == 0
        except Exception as e:
            stdout = ""
            stderr = str(e)
            rc = 2
            ok = False
        if rc == 130:
            append_worklog("cancel", f"rc=130 remote {cmd}")
        else:
            append_worklog("done", f"rc={rc} remote {cmd}")
        return ok, stdout, stderr, rc

    def _format_review_response(text: str) -> str:
        if not text:
            return "Findings:\n- None.\n\nQuestions:\n- None.\n\nTests:\n- Not run."
        import re as _re
        cmd_re = _re.compile(r"^\s*command:\s*.+", _re.IGNORECASE)
        lines = text.splitlines()
        cmd_lines = [line for line in lines if cmd_re.match(line)]
        body_lines = [line for line in lines if not cmd_re.match(line)]
        body = "\n".join(body_lines).strip()
        lower = body.lower()
        has_findings = "findings" in lower
        has_questions = "questions" in lower
        has_tests = "tests" in lower or "testing" in lower
        if has_findings and has_questions and has_tests:
            return text
        formatted = (
            "Findings:\n"
            "- " + (body.replace("\n", "\n- ") if body else "None.") + "\n\n"
            "Questions:\n"
            "- None.\n\n"
            "Tests:\n"
            "- Not run."
        )
        if cmd_lines:
            formatted = formatted + "\n\n" + "\n".join(cmd_lines)
        return formatted

    def _print_output_summary(output: str, label: str = "Output summary") -> None:
        if not output:
            return
        summary, _redacted = _summarize_text(output, max_len=220)
        if summary:
            print(f"martin: {label}: {summary}")

    def _outside_workspace_path(cmd: str) -> Optional[str]:
        ws = Path.cwd().resolve()
        try:
            tokens = shlex.split(cmd)
        except Exception:
            tokens = cmd.split()
        skip = {"&&", "||", "|", ">", ">>", "<", "<<", ";", "&"}
        for idx, tok in enumerate(tokens):
            if tok in skip or tok.startswith("-"):
                continue
            candidate = tok
            if tok.lower() in ("cd", "set-location", "pushd") and idx + 1 < len(tokens):
                candidate = tokens[idx + 1]
            expanded = os.path.expandvars(os.path.expanduser(candidate))
            try:
                path = Path(expanded)
            except Exception:
                continue
            if not path.is_absolute():
                continue
            try:
                resolved = path.resolve()
            except Exception:
                resolved = path
            if resolved != ws and ws not in resolved.parents:
                return str(resolved)
        return None

    def _confirm_outside_workspace(target: str, cmd: str) -> bool:
        exec_cfg = cfg.get("execution", {}) or {}
        hard_block_outside = bool(exec_cfg.get("hard_block_outside"))
        if hard_block_outside:
            print("\033[93mmartin: Outside-workspace writes are hard-blocked by policy.\033[0m")
            try:
                log_event(load_state(), "workspace_boundary", cmd=cmd, target=target, allowed=False, hard_block=True)
            except Exception:
                pass
            return False
        if approval_policy == "never":
            return False
        try:
            resp = input(f"\033[93mmartin: Command touches outside workspace ({target}). Proceed? (yes/no)\033[0m ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            resp = "no"
        ok = resp == "yes"
        try:
            log_event(load_state(), "workspace_boundary", cmd=cmd, target=target, allowed=ok)
        except Exception:
            pass
        return ok

    def _execute_command_with_policy(cmd: str, label: str = "command") -> Tuple[bool, str, str, int, str, float]:
        exec_cfg = cfg.get("execution", {}) or {}
        approval_policy = (exec_cfg.get("approval_policy") or "on-request").lower()
        sandbox_mode = (exec_cfg.get("sandbox_mode") or "workspace-write").lower()
        command_allowlist = exec_cfg.get("command_allowlist") or []
        command_denylist = exec_cfg.get("command_denylist") or []
        remote_policy = (exec_cfg.get("remote_policy") or "block").lower()
        try:
            st = load_state()
            current_host = st.get("current_host", "") if isinstance(st, dict) else ""
        except Exception:
            current_host = ""
        if current_host and current_host != "local" and remote_policy == "block":
            try:
                log_event(load_state(), "remote_policy_block", host=current_host, cmd=cmd)
            except Exception:
                pass
            print("\033[93mmartin: Remote relay policy blocks execution on non-local host.\033[0m")
            return False, "", "remote_policy_block", 2, "", 0.0
        outside = _outside_workspace_path(cmd)
        if outside:
            if not _confirm_outside_workspace(outside, cmd):
                return False, "", "outside_workspace_blocked", 2, "", 0.0
        risk = classify_command_risk(cmd, command_allowlist, command_denylist)
        if risk["level"] == "blocked":
            print(f"\033[93mmartin: {label} blocked by policy.\033[0m")
            return False, "", "blocked by policy", 2, "", 0.0
        allowed, reason = enforce_sandbox(cmd, sandbox_mode, str(Path.cwd()))
        if not allowed:
            if approval_policy != "never":
                try:
                    resp = input(f"\033[93mmartin: Sandbox blocked this {label} ({reason}). Override once? (yes/no)\033[0m ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    resp = "no"
                allowed = resp == "yes"
            if not allowed:
                print(f"\033[93mmartin: {label} blocked by sandbox.\033[0m")
                return False, "", reason, 2, "", 0.0
        if approval_policy == "on-request":
            try:
                confirm = input(f"\033[93mApprove running this {label}? (yes/no)\033[0m ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                confirm = "no"
            if confirm != "yes":
                print("\033[92mmartin: Aborting per approval policy.\033[0m")
                return False, "", "aborted", 1, "", 0.0
        t0 = time.perf_counter()
        if current_host and current_host != "local" and remote_policy == "relay":
            ok, stdout, stderr, rc = _run_remote_cmd(cmd)
            try:
                log_event(load_state(), "remote_command", host=current_host, cmd=cmd, rc=rc)
            except Exception:
                pass
        else:
            ok, stdout, stderr, rc = _run_cmd_with_worklog(cmd)
        duration = time.perf_counter() - t0
        if rc == 130:
            print("\033[93mmartin: Command cancelled.\033[0m")
        output_path = ""
        if stdout and len(stdout) > 4000 and not _privacy_enabled():
            output_path = _store_long_output(stdout, label)
        if stdout:
            print(_format_output_for_display(stdout))
        if stderr:
            print(_format_output_for_display(stderr), file=sys.stderr)
        combined_output = stdout or ""
        if stderr:
            combined_output = (combined_output + "\n" + stderr).strip()
        _print_output_summary(combined_output)
        if rc and rc != 0:
            _record_failed_command(cmd, rc, stderr or "failed")
        if not _privacy_enabled():
            try:
                append_tool_entry({
                    "command": cmd,
                    "cwd": str(Path.cwd()),
                    "rc": rc,
                    "ok": ok,
                    "duration_s": duration,
                    "stdout": stdout,
                    "stderr": stderr,
                    "output_path": output_path,
                    "risk": risk.get("level"),
                    "risk_reasons": risk.get("reasons"),
                    "sandbox_mode": sandbox_mode,
                    "approval_policy": approval_policy,
                })
            except Exception:
                pass
        return ok, stdout, stderr, rc, output_path, duration

    def _mo_exit_check() -> None:
        st = load_state()
        session_start = st.get("session_start_ts", "") if isinstance(st, dict) else ""
        last_signoff = st.get("last_signoff_ts", "") if isinstance(st, dict) else ""
        if not last_signoff or (session_start and last_signoff < session_start):
            print("martin: Reminder: run /signoff for a session summary.")
    def _privacy_enabled() -> bool:
        try:
            st = load_state()
            return bool(st.get("session_privacy") == "no-log")
        except Exception:
            return False
    def _record_failed_command(cmd: str, rc: int, reason: str) -> None:
        if _privacy_enabled():
            return
        try:
            st = load_state()
            st["last_failed_command"] = {
                "cmd": cmd,
                "rc": rc,
                "reason": reason,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "acked": False,
            }
            save_state(st)
        except Exception:
            pass

    def _maybe_prompt_retry() -> None:
        if _privacy_enabled():
            return
        if compact_startup and not (Path.cwd() / ".git").exists():
            return
        try:
            st = load_state()
            last_fail = st.get("last_failed_command", {})
            if last_fail and not last_fail.get("acked"):
                cmd = last_fail.get("cmd", "")
                rc = last_fail.get("rc", "")
                print(f"\033[93mmartin: Last command failed (rc={rc}). Use /retry to rerun.\033[0m")
                if cmd:
                    print(f"martin: Last failed command: {cmd}")
        except Exception:
            pass
    # Start the socket server
    socket_server_cfg = cfg.get("socket_server", {})
    server = SocketServer(
        host=socket_server_cfg.get("host"),
        port=socket_server_cfg.get("port"),
        handler=_handle_librarian_notification,
        verbose=bool(socket_server_cfg.get("verbose", False)),
    )
    server.start()
    test_bridge = None
    test_cfg = cfg.get("test_socket", {}) or {}
    test_enabled = bool(test_cfg.get("enabled")) or os.environ.get("MARTIN_TEST_SOCKET") == "1"
    original_input = None
    if test_enabled:
        token_env = test_cfg.get("token_env", "MARTIN_TEST_SOCKET_TOKEN")
        token_value = os.environ.get(token_env, "") if token_env else ""
        test_bridge = TestSocketBridge(
            host=test_cfg.get("host", "127.0.0.1"),
            port=int(test_cfg.get("port", 7002)),
            fallback_to_stdin=bool(test_cfg.get("fallback_to_stdin", False)),
            timeout_s=float(test_cfg.get("timeout_s") or 0.0),
            token=token_value,
            allow_non_loopback=bool(test_cfg.get("allow_non_loopback", False)),
        )
        test_bridge.start()
        test_bridge.install_streams()
        original_input = builtins.input
        builtins.input = test_bridge.read_input
    read_user_input = test_bridge.read_input if test_bridge else input
    try:
        st = load_state()
        sess = SessionCtx(st)
        sess.begin()
        try:
            st = load_state()
            st["session_start_ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            save_state(st)
        except Exception:
            pass
        total_steps = 4
        _startup_progress(1, total_steps, "preflight", "start")
        _mo_preflight_check()
        _startup_progress(1, total_steps, "preflight", "done")
        _startup_progress(2, total_steps, "clock-in", "start")
        _prompt_clock("Clock-in")
        _startup_progress(2, total_steps, "clock-in", "done")
        _startup_progress(3, total_steps, "context", "start")
        _auto_context_surface("session start", quiet=compact_startup)
        _startup_progress(3, total_steps, "context", "done")
        _maybe_prompt_retry()
        try:
            st = load_state()
            _startup_progress(4, total_steps, "onboarding", "start")
            if not st.get("onboarding_complete"):
                _run_onboarding()
            _startup_progress(4, total_steps, "onboarding", "done")
        except Exception:
            pass
        logger = _get_cli_logger(cfg)
        verbose_logging = _logging_verbose(cfg)
        behavior_flags = _behavior_cfg(cfg)
        ui_flags = _ui_cfg(cfg)
        logger.info("chat_start")
        if test_bridge:
            try:
                test_bridge.send_event({"type": "phase", "text": "chat_start"})
            except Exception:
                pass
        last_user_request = ""
        agent_mode = False
        cloud_enabled = bool(cfg.get("cloud", {}).get("enabled"))
        if cfg.get("local_only"):
            cloud_enabled = False
        trust_policy = cfg.get("trust_policy", {}) or {}
        if not trust_policy.get("allow_cloud", False):
            cloud_enabled = False
        exec_cfg = cfg.get("execution", {}) or {}
        approval_policy = (exec_cfg.get("approval_policy") or "on-request").lower()
        sandbox_mode = (exec_cfg.get("sandbox_mode") or "workspace-write").lower()
        command_allowlist = exec_cfg.get("command_allowlist") or []
        command_denylist = exec_cfg.get("command_denylist") or []
        def _maybe_override_sandbox(block_reason: str) -> bool:
            if approval_policy == "never":
                return False
            try:
                resp = input(f"\033[93mmartin: Sandbox blocked this command ({block_reason}). Override once? (yes/no)\033[0m ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                resp = "no"
            return resp == "yes"
        session_transcript = []
        slash_commands = chat_ui.get_slash_commands()
        command_descriptions = chat_ui.get_command_descriptions()
        if "/files" not in slash_commands:
            slash_commands.append("/files")
        command_descriptions.setdefault("/files", "file picker")
        last_palette_entries = []
        last_file_entries = []
        context_cache = {}
        # Load persisted memory (best-effort).
        mem = st.get("memory", {}) if isinstance(st, dict) else {}
        if isinstance(mem, dict):
            global _LAST_PATH, _LAST_LISTING
            _LAST_PATH = mem.get("last_path", "") or _LAST_PATH
            _LAST_LISTING = mem.get("last_listing", []) or _LAST_LISTING
        if isinstance(st, dict):
            cached_context = st.get("context_cache")
            if isinstance(cached_context, dict):
                context_cache = cached_context

        transcript = []
        def _apply_resume_snapshot(snapshot: Dict[str, Any]) -> None:
            if not isinstance(snapshot, dict):
                return
            global _LAST_PATH, _LAST_LISTING
            _LAST_PATH = snapshot.get("last_path", _LAST_PATH) or _LAST_PATH
            _LAST_LISTING = snapshot.get("last_listing", _LAST_LISTING) or _LAST_LISTING
            if isinstance(snapshot.get("context_cache"), dict):
                context_cache.update(snapshot.get("context_cache") or {})
            if "last_plan" in snapshot:
                try:
                    st = load_state()
                    st["last_plan"] = snapshot.get("last_plan") or st.get("last_plan")
                    save_state(st)
                except Exception:
                    pass
        snapshot = None
        try:
            snapshot = st.get("resume_snapshot")
        except Exception:
            snapshot = None
        if snapshot:
            _apply_resume_snapshot(snapshot)
            print(f"\nmartin: Resumed previous session from {snapshot.get('ts', 'unknown')}.")
        else:
            print("\nmartin: Welcome! Type 'quit' to exit.")
        try:
            if not context_cache:
                from researcher.context_harvest import gather_context
                fast_ctx = not (Path.cwd() / ".git").exists()
                context_cache = gather_context(Path.cwd(), max_recent=int(cfg.get("context", {}).get("max_recent", 10)), fast=fast_ctx)
                st = load_state()
                st["context_cache"] = context_cache
                save_state(st)
            chat_ui.print_context_summary(context_cache)
            try:
                last_cmd_summary = load_state().get("last_command_summary", {}) or {}
            except Exception:
                last_cmd_summary = {}
            warn = "local-only" if cfg.get("local_only") else ("cloud-off" if not cloud_enabled else "")
            active_context = {}
            try:
                if behavior_flags.get("context_block"):
                    st = load_state()
                    active_context = _build_active_context(st)
            except Exception:
                active_context = {}
            try:
                st = load_state()
                current_host = st.get("current_host", "") if isinstance(st, dict) else ""
            except Exception:
                current_host = ""
            def _render_footer() -> None:
                try:
                    st_local = load_state()
                    last_cmd = st_local.get("last_command_summary", {}) or {}
                    active_ctx = _build_active_context(st_local) if behavior_flags.get("context_block") else {}
                    warn_local = "local-only" if cfg.get("local_only") else ("cloud-off" if not cloud_enabled else "")
                    host_local = st_local.get("current_host", "") if isinstance(st_local, dict) else ""
                    chat_ui.render_status_banner(
                        context_cache,
                        last_cmd,
                        mode=("agent" if agent_mode else "manual"),
                        model_info=MODEL_MAIN,
                        warnings=warn_local,
                        active_context=active_ctx,
                        current_host=host_local,
                    )
                except Exception:
                    pass
            chat_ui.render_status_banner(
                context_cache,
                last_cmd_summary,
                mode=("agent" if agent_mode else "manual"),
                model_info=MODEL_MAIN,
                warnings=warn,
                active_context=active_context,
                current_host=current_host,
            )
        except Exception:
            pass
        readline_mod = None
        history_path = None
        readline_mod, history_path = chat_ui.setup_readline(cfg, slash_commands)
        try:
            if cfg.get("vector_store", {}).get("warm_on_start"):
                _load_index(cfg)
        except Exception:
            pass
        try:
            if cfg.get("context", {}).get("auto"):
                from researcher.context_harvest import gather_context
                fast_ctx = not (Path.cwd() / ".git").exists()
                context_cache = gather_context(Path.cwd(), max_recent=int(cfg.get("context", {}).get("max_recent", 10)), fast=fast_ctx)
                st = load_state()
                st["context_cache"] = context_cache
                save_state(st)
        except Exception:
            pass

        should_exit = False
        try:
            if cfg.get("local_only") and (os.environ.get("RESEARCHER_CLOUD_API_KEY") or os.environ.get("OPENAI_API_KEY")):
                print("\033[93mmartin: local-only mode is ON; cloud credentials are present but will be ignored.\033[0m")
        except Exception:
            pass
        if test_bridge:
            try:
                test_bridge.send_event({"type": "loop_ready"})
            except Exception:
                pass
        while True:
            try:
                st = load_state()
                if st.get("librarian_unread"):
                    print("\033[92mmartin: Librarian has updates. Use /librarian inbox.\033[0m")
                    st["librarian_unread"] = False
                    save_state(st)
                tasks = st.get("tasks", [])
                if tasks and not st.get("tasks_prompted"):
                    print(f"\033[92mmartin: Next task: {tasks[0].get('text','')}\033[0m")
                    st["tasks_prompted"] = True
                    save_state(st)
            except Exception:
                pass
            try:
                if test_bridge:
                    try:
                        test_bridge.send_event({"type": "input_wait"})
                    except Exception:
                        pass
                user_input = read_user_input("\033[94mYou:\033[0m ")
            except (EOFError, KeyboardInterrupt):
                print("\n\033[92mmartin: Farewell.\033[0m")
                logger.info("chat_end reason=interrupt")
                break
            original_user_input = user_input

            if user_input.lower() in ('quit', 'exit'):
                print("\033[92mmartin: Goodbye!\033[0m")
                logger.info("chat_end reason=quit")
                break
            logger.info("chat_input len=%d", len(user_input))
            try:
                summary, redacted = _summarize_user_input(original_user_input)
                if summary:
                    logger.info("chat_input summary=%s redacted=%s", summary, redacted)
                log_event(load_state(), "chat_input", length=len(user_input), summary=summary, redacted=redacted)
            except Exception:
                pass
            try:
                if not _privacy_enabled_state() and behavior_flags.get("followup_resolver"):
                    if not original_user_input.strip().startswith("/"):
                        st = load_state()
                        if not st.get("review_mode"):
                            goal = st.get("active_goal", "") if isinstance(st, dict) else ""
                            if _is_followup(original_user_input) or (_is_short_followup(original_user_input) and goal):
                                last_action = st.get("last_action_summary", "") if isinstance(st, dict) else ""
                                if goal:
                                    user_input = f"Continue the active goal: {goal}. Last action: {last_action}"
                                    log_event(load_state(), "followup_resolved", goal=goal, last_action=last_action)
            except Exception:
                pass
            try:
                if not _privacy_enabled_state() and not _is_followup(original_user_input):
                    st = load_state()
                    _maybe_update_goal(st, original_user_input, force=False)
                    if behavior_flags.get("context_block"):
                        st["active_context"] = _build_active_context(st)
                    save_state(st)
            except Exception:
                pass

            def _is_disagreement(text: str) -> bool:
                phrases = cfg.get("cloud", {}).get("disagreement_phrases", []) or []
                lowered = text.strip().lower()
                if not lowered:
                    return False
                return any(p in lowered for p in phrases)

            def _handle_slash(cmd: str) -> bool:
                nonlocal agent_mode, cloud_enabled, transcript, should_exit
                if not cmd.startswith("/"):
                    return False
                parts = shlex.split(cmd)
                if not parts:
                    return True
                name = parts[0].lstrip("/").lower()
                args = parts[1:]
                if name == "":
                    name = "help"
                if name in ("exit", "quit"):
                    print("\033[92mmartin: Goodbye!\033[0m")
                    logger.info("chat_end reason=slash_exit")
                    should_exit = True
                    return True
                if name == "help":
                    print("Commands: /help, /clear, /status, /memory, /history, /palette, /files, /open <path>:<line>, /worklog, /clock in|out, /privacy on|off|status, /keys, /retry, /onboarding, /verify, /context [refresh], /goal status|set <text>|clear, /plan, /outputs [ledger|export <path>|search <text>], /export session <path>, /import session <path>, /resume, /librarian inbox|request <topic>|sources <topic>|accept <n>|dismiss <n>, /rag status, /tasks add|list|done <n>, /review on|off, /abilities, /resources, /resource <path>, /tests, /rerun [command|test], /agent on|off|status, /cloud on|off, /ask <q>, /ingest <path>, /host list|pair|use, /remote start|stop|status|config, /redaction report [days], /trust keygen, /encrypt <path>, /decrypt <path>, /rotate <path> <old_env> <new_env>, /compress, /signoff, /exit")
                    print("martin: UX behaviors: docs/ux_behaviors.md")
                    print("martin: Expected behavior: docs/expected_behavior.md")
                    return True
                if name == "clear":
                    transcript = []
                    interaction_history.clear()
                    print("martin: Cleared transcript.")
                    return True
                if name == "compress":
                    if not transcript:
                        print("martin: No transcript to compress.")
                        return True
                    summary = rephraser("\n".join(transcript)[-4000:])
                    print("martin: Compressed summary:")
                    print(summary)
                    return True
                if name == "worklog":
                    items = read_worklog(10)
                    if not items:
                        print("martin: No worklog entries yet.")
                        return True
                    print("martin: Worklog (last 10)")
                    for entry in items:
                        print(f"- {entry.get('ts','')} {entry.get('kind','')}: {entry.get('text','')}")
                    return True
                if name == "queue":
                    try:
                        st = load_state()
                        queue = st.get("action_queue", []) if isinstance(st, dict) else []
                    except Exception:
                        queue = []
                    _render_action_queue(queue if isinstance(queue, list) else [])
                    return True
                if name == "clock":
                    sub = args[0].lower() if args else ""
                    if sub in ("in", "clock-in"):
                        _prompt_clock("Clock-in")
                        return True
                    if sub in ("out", "clock-out"):
                        _prompt_clock("Clock-out")
                        return True
                    print("martin: Use /clock in or /clock out.")
                    return True
                if name == "privacy":
                    st = load_state()
                    sub = args[0].lower() if args else "status"
                    if sub == "status":
                        mode = st.get("session_privacy", "off")
                        print(f"martin: privacy mode = {mode}")
                        return True
                    if sub == "on":
                        st["session_privacy"] = "no-log"
                        save_state(st)
                        print("martin: privacy mode enabled (no-log).")
                        return True
                    if sub == "off":
                        st["session_privacy"] = "off"
                        save_state(st)
                        print("martin: privacy mode disabled.")
                        return True
                    print("martin: Use /privacy on|off|status.")
                    return True
                if name == "keys":
                    print("martin: Keybindings")
                    print("TUI: q quit, p palette, t tasks, o outputs, m process, c context, r refresh, f filter outputs, j/k or arrows move, a add task, x done task, ? help.")
                    print("Chat: use /help for slash commands.")
                    return True
                if name == "retry":
                    st = load_state()
                    last_fail = st.get("last_failed_command", {}) if isinstance(st, dict) else {}
                    cmd = last_fail.get("cmd")
                    if not cmd:
                        print("martin: No failed command recorded.")
                        return True
                    ok, stdout, stderr, rc, output_path, duration = _execute_command_with_policy(cmd, label="retry command")
                    try:
                        st = load_state()
                        st["last_failed_command"]["acked"] = True
                        st["last_command_summary"] = {
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "cmd": cmd,
                            "rc": rc,
                            "ok": ok,
                            "summary": chat_ui.shorten_output(stdout or stderr),
                        }
                        save_state(st)
                    except Exception:
                        pass
                    return True
                if name == "onboarding":
                    _run_onboarding()
                    return True
                if name == "verify":
                    venv_root = Path(".venv")
                    py_path = venv_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                    pytest_ok = False
                    if py_path.exists():
                        try:
                            res = subprocess.run([str(py_path), "-m", "pytest", "--version"], capture_output=True, text=True, check=False)
                            pytest_ok = res.returncode == 0
                        except Exception:
                            pytest_ok = False
                    install_script = Path("scripts") / "install_martin.ps1"
                    service_script = Path("scripts") / "martin_service.ps1"
                    trust = cfg.get("trust_policy", {}) or {}
                    key_env = trust.get("encryption_key_env", "MARTIN_ENCRYPTION_KEY")
                    key_set = bool(os.environ.get(key_env or ""))
                    next_steps = []
                    if not py_path.exists():
                        next_steps.append("run scripts/install_martin.ps1")
                    if py_path.exists() and not pytest_ok:
                        next_steps.append("run scripts/run_tests.ps1")
                    if not key_set and trust.get("encrypt_exports"):
                        next_steps.append(f"set {key_env} env var for encryption")
                    report = {
                        "venv_python": str(py_path) if py_path.exists() else "",
                        "pytest_available": pytest_ok,
                        "install_script": str(install_script) if install_script.exists() else "",
                        "service_script": str(service_script) if service_script.exists() else "",
                        "remote_transport": validate_transport(cfg),
                        "encryption_key_set": key_set,
                        "next_steps": next_steps,
                    }
                    print(json.dumps(report, ensure_ascii=False, indent=2))
                    return True
                if name == "signoff":
                    if transcript:
                        summary = rephraser("\n".join(transcript)[-4000:])
                    else:
                        summary = "No transcript captured."
                    print("martin: Signoff")
                    print(summary)
                    try:
                        st = load_state()
                        st["last_signoff_ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                        save_state(st)
                    except Exception:
                        pass
                    print("martin: Session complete. Anything else?")
                    return True
                if name == "status":
                    payload = get_status_payload(cfg, force_simple=False)
                    print(json.dumps(payload, ensure_ascii=False))
                    return True
                if name == "memory":
                    st = load_state()
                    payload = {
                        "memory": st.get("memory", {}),
                        "history": st.get("memory_history", []),
                        "session_memory": st.get("session_memory", {}),
                        "session_history": st.get("session_history", []),
                    }
                    print(json.dumps(payload, ensure_ascii=False, indent=2))
                    return True
                if name == "history":
                    picked = chat_ui.handle_history_command(args, session_transcript, readline_mod, history_path)
                    if picked:
                        last_user_request = picked
                    return True
                if name == "palette":
                    query = " ".join(args).strip().lower()
                    if args and args[0].lower() == "pick":
                        try:
                            idx = int(args[1]) if len(args) > 1 else 0
                        except Exception:
                            idx = 0
                        entries = last_palette_entries
                        if not entries:
                            entries = chat_ui.render_palette("", slash_commands, command_descriptions, session_transcript)
                            last_palette_entries = entries
                        if not (1 <= idx <= len(entries)):
                            print("martin: Use /palette pick <n> from the last palette view.")
                            return True
                        kind, value = entries[idx - 1]
                        if kind == "cmd":
                            print(f"martin: Picked command: {value}")
                            print("martin: Paste it or press Up arrow to reuse.")
                            last_user_request = value
                        else:
                            picked = value.replace("You: ", "", 1)
                            last_user_request = picked
                            print(f"martin: Picked input: {value}")
                            print("martin: Press Up arrow to edit/reuse.")
                        return True
                    last_palette_entries = chat_ui.render_palette(query, slash_commands, command_descriptions, session_transcript)
                    return True
                if name == "files":
                    query = " ".join(args).strip().lower()
                    if args and args[0].lower() == "pick":
                        try:
                            idx = int(args[1]) if len(args) > 1 else 0
                        except Exception:
                            idx = 0
                        if not last_file_entries:
                            last_file_entries = chat_ui.build_file_entries("")
                            chat_ui.render_file_picker(last_file_entries)
                        if not (1 <= idx <= len(last_file_entries)):
                            print("martin: Use /files pick <n> from the last file list.")
                            return True
                        picked = last_file_entries[idx - 1]
                        last_user_request = picked
                        print(f"martin: Picked file: {picked}")
                        print("martin: Press Up arrow to edit/reuse.")
                        return True
                    last_file_entries = chat_ui.build_file_entries(query)
                    if not last_file_entries:
                        print("martin: No files found.")
                        return True
                    chat_ui.render_file_picker(last_file_entries)
                    return True
                if name == "open":
                    from researcher.file_utils import render_snippet
                    if not args:
                        print("martin: Use /open <path>:<line>.")
                        return True
                    target = " ".join(args).strip()
                    path_part = target
                    line_no = None
                    if ":" in target:
                        left, right = target.rsplit(":", 1)
                        if right.isdigit():
                            path_part = left
                            try:
                                line_no = int(right)
                            except Exception:
                                line_no = None
                    path = Path(os.path.expanduser(os.path.expandvars(path_part)))
                    if not path.exists():
                        print(f"martin: File not found: {path}")
                        return True
                    try:
                        ws = Path.cwd().resolve()
                        resolved = path.resolve()
                        if resolved != ws and ws not in resolved.parents:
                            if not _confirm_outside_workspace(str(resolved), f"open {resolved}"):
                                print("martin: Open cancelled (outside workspace).")
                                return True
                    except Exception:
                        pass
                    print(render_snippet(path, line_no))
                    return True
                if name == "plan":
                    st = load_state()
                    payload = st.get("last_plan", {})
                    if isinstance(payload, dict):
                        payload = dict(payload)
                        rationale = st.get("last_plan_rationale", "")
                        if rationale:
                            payload["rationale"] = rationale
                    print(json.dumps(payload, ensure_ascii=False, indent=2))
                    return True
                if name == "outputs":
                    if args and args[0] == "search":
                        query = " ".join(args[1:]).strip()
                        if not query:
                            print("martin: Use /outputs search <text>.")
                            return True
                        rows = read_recent(limit=20, filters={"text": query})
                        if not rows:
                            print("martin: No matching outputs.")
                            return True
                        for row in rows:
                            entry = row.get("entry", {})
                            cmd = entry.get("command", "")
                            ts = entry.get("ts", "")
                            rc = entry.get("rc", "")
                            out_path = entry.get("output_path", "")
                            print(f"{ts} rc={rc} cmd={cmd}")
                            if out_path:
                                print(f"  output: {out_path}")
                        return True
                    if args and args[0] == "ledger":
                        filters: Dict[str, Any] = {}
                        for tok in args[1:]:
                            if tok.startswith("--rc="):
                                try:
                                    filters["rc"] = int(tok.split("=", 1)[1])
                                except Exception:
                                    pass
                            elif tok.startswith("--rc!="):
                                try:
                                    filters["rc_not"] = int(tok.split("!=", 1)[1])
                                except Exception:
                                    pass
                            elif tok.startswith("--risk="):
                                filters["risk"] = tok.split("=", 1)[1]
                            elif tok.startswith("--cwd="):
                                filters["cwd"] = tok.split("=", 1)[1]
                            elif tok.startswith("--text="):
                                filters["text"] = tok.split("=", 1)[1]
                            elif tok.startswith("--since="):
                                val = tok.split("=", 1)[1]
                                unit = val[-1:]
                                try:
                                    num = int(val[:-1])
                                    seconds = num
                                    if unit == "h":
                                        seconds = num * 3600
                                    elif unit == "m":
                                        seconds = num * 60
                                    elif unit == "s":
                                        seconds = num
                                    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - seconds))
                                    filters["since"] = ts
                                except Exception:
                                    pass
                        rows = read_recent(limit=10, filters=filters)
                        if not rows:
                            print("martin: Tool ledger is empty.")
                            return True
                        for row in rows:
                            entry = row.get("entry", {})
                            cmd = entry.get("command", "")
                            ts = entry.get("ts", "")
                            rc = entry.get("rc", "")
                            print(f"{ts} rc={rc} cmd={cmd}")
                        return True
                    if args and args[0] == "export":
                        if _privacy_enabled():
                            print("martin: Privacy mode is on; ledger export is disabled.")
                            return True
                        out_path = args[1] if len(args) > 1 else str(Path("logs") / "tool_ledger_export.json")
                        try:
                            content = build_export_json()
                            try:
                                st = load_state()
                                current_host = st.get("current_host", "") if isinstance(st, dict) else ""
                            except Exception:
                                current_host = ""
                            enc_cfg = _encryption_policy(cfg, current_host)
                            if enc_cfg.get("encrypt"):
                                from researcher.crypto_utils import encrypt_text
                                key_env = enc_cfg.get("key_env")
                                key = os.environ.get(key_env or "")
                                if not key:
                                    print("martin: Encryption key not set; export blocked.")
                                    return True
                                content = encrypt_text(content, key)
                                out_path = out_path + ".enc" if not out_path.endswith(".enc") else out_path
                            if preview_write(Path(out_path), content):
                                Path(out_path).write_text(content, encoding="utf-8")
                                print(f"martin: Exported tool ledger to {out_path}")
                            else:
                                print("martin: Export cancelled.")
                        except Exception as e:
                            print(f"martin: Export failed ({e})")
                        return True
                    try:
                        files = sorted(_OUTPUT_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)[:10]
                        for p in files:
                            print(str(p))
                    except Exception:
                        print("martin: No outputs found.")
                    return True
                if name == "resume":
                    snapshot = None
                    try:
                        st = load_state()
                        snapshot = st.get("resume_snapshot")
                    except Exception:
                        snapshot = None
                    if not snapshot:
                        print("martin: No resume snapshot found.")
                        return True
                    _apply_resume_snapshot(snapshot)
                    print(json.dumps(snapshot, ensure_ascii=False, indent=2))
                    return True
                if name == "abilities":
                    try:
                        from researcher.orchestrator import ABILITY_REGISTRY
                        payload = {"abilities": sorted(list(ABILITY_REGISTRY.keys()))}
                        print(json.dumps(payload, ensure_ascii=False, indent=2))
                    except Exception:
                        print("martin: Unable to load abilities.")
                    return True
                if name == "resources":
                    payload = list_resources()
                    print(json.dumps({"root": str(ROOT_DIR), "items": payload}, ensure_ascii=False, indent=2))
                    return True
                if name == "resource":
                    if not args:
                        print("martin: Provide a resource path.")
                        return True
                    path = " ".join(args)
                    ok, result = read_resource(path)
                    result["ok"] = ok
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                    return True
                if name == "tests":
                    try:
                        from researcher.test_helpers import suggest_test_commands
                        cmds = suggest_test_commands(Path.cwd())
                        st = load_state()
                        last_test = st.get("tests_last", {}) if isinstance(st, dict) else {}
                        if last_test:
                            status = "ok" if last_test.get("ok") else "fail"
                            print(f"martin: Last test: {status} rc={last_test.get('rc')} ({last_test.get('duration_s', 0):.2f}s) {last_test.get('cmd')}")
                        if args and args[0].lower() == "run":
                            if not cmds:
                                print("martin: No test helpers detected in this folder.")
                                return True
                            try:
                                idx = int(args[1]) if len(args) > 1 else 0
                            except Exception:
                                idx = 0
                            if not (1 <= idx <= len(cmds)):
                                print("martin: Use /tests run <n> from the suggested list.")
                                return True
                            cmd = cmds[idx - 1]
                            ok, stdout, stderr, rc, output_path, duration = _execute_command_with_policy(cmd, label="test")
                            try:
                                st = load_state()
                                st["tests_last"] = {
                                    "cmd": cmd,
                                    "rc": rc,
                                    "ok": ok,
                                    "duration_s": round(duration, 3),
                                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                }
                                save_state(st)
                                log_event(st, "tests_run", cmd=cmd, ok=ok, rc=rc, duration_s=duration)
                            except Exception:
                                pass
                            return True
                        if not cmds:
                            print("martin: No test helpers detected in this folder.")
                            return True
                        print("martin: Suggested test/run commands (use /tests run <n>):")
                        for i, c in enumerate(cmds, 1):
                            print(f"{i}. {c}")
                    except Exception:
                        print("martin: Unable to suggest tests here.")
                    return True
                if name == "rerun":
                    sub = args[0].lower() if args else "command"
                    st = load_state()
                    if sub in ("command", "last"):
                        cmd = (st.get("last_command_summary", {}) or {}).get("cmd")
                        if not cmd:
                            print("martin: No last command recorded.")
                            return True
                        ok, stdout, stderr, rc, output_path, duration = _execute_command_with_policy(cmd, label="rerun command")
                        try:
                            st = load_state()
                            st["last_command_summary"] = {
                                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                "cmd": cmd,
                                "rc": rc,
                                "ok": ok,
                                "summary": chat_ui.shorten_output(stdout or stderr),
                            }
                            save_state(st)
                        except Exception:
                            pass
                        return True
                    if sub == "test":
                        cmd = (st.get("tests_last", {}) or {}).get("cmd")
                        if not cmd:
                            print("martin: No last test recorded.")
                            return True
                        ok, stdout, stderr, rc, output_path, duration = _execute_command_with_policy(cmd, label="rerun test")
                        try:
                            st = load_state()
                            st["tests_last"] = {
                                "cmd": cmd,
                                "rc": rc,
                                "ok": ok,
                                "duration_s": round(duration, 3),
                                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            }
                            save_state(st)
                        except Exception:
                            pass
                        return True
                    print("martin: Use /rerun command or /rerun test.")
                    return True
                if name == "tasks":
                    st = load_state()
                    tasks = st.get("tasks", [])
                    if not args:
                        print("martin: Use /tasks add <text>, /tasks list, or /tasks done <n>.")
                        return True
                    action = args[0].lower()
                    if action == "list":
                        if not tasks:
                            print("martin: No open tasks.")
                            return True
                        for idx, t in enumerate(tasks, 1):
                            print(f"{idx}. {t.get('text','')}")
                        return True
                    if action == "add":
                        text = " ".join(args[1:]).strip()
                        if not text:
                            print("martin: Provide task text.")
                            return True
                        tasks.append({"text": text, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
                        st["tasks"] = tasks[-100:]
                        st.pop("tasks_prompted", None)
                        save_state(st)
                        print("martin: Task added.")
                        return True
                    if action == "done":
                        if len(args) < 2:
                            print("martin: Provide a task index.")
                            return True
                        try:
                            idx = int(args[1])
                        except ValueError:
                            print("martin: Invalid index.")
                            return True
                        if not (1 <= idx <= len(tasks)):
                            print("martin: Index out of range.")
                            return True
                        task = tasks.pop(idx - 1)
                        st["tasks"] = tasks
                        st.pop("tasks_prompted", None)
                        save_state(st)
                        print(f"martin: Completed '{task.get('text','')}'.")
                        return True
                    print("martin: Unknown /tasks action.")
                    return True
                if name == "review":
                    if not args:
                        print("martin: Use /review on or /review off.")
                        return True
                    mode = args[0].lower()
                    if mode not in ("on", "off"):
                        print("martin: Use /review on or /review off.")
                        return True
                    st = load_state()
                    st["review_mode"] = (mode == "on")
                    save_state(st)
                    print(f"martin: Review mode {mode}.")
                    return True
                if name == "rag":
                    if not args or args[0].lower() != "status":
                        print("martin: Use /rag status.")
                        return True
                    st = load_state()
                    inbox = st.get("librarian_inbox", [])
                    gaps = []
                    try:
                        if LEDGER_FILE.exists():
                            with open(LEDGER_FILE, "r", encoding="utf-8") as f:
                                lines = f.readlines()
                            for line in reversed(lines):
                                try:
                                    record = json.loads(line)
                                    entry = record.get("entry", {})
                                    if entry.get("event") == "rag_gap":
                                        gaps.append(entry.get("data", {}))
                                    if len(gaps) >= 5:
                                        break
                                except Exception:
                                    continue
                    except Exception:
                        pass
                    payload = {
                        "inbox_count": len(inbox),
                        "recent_gaps": gaps,
                        "last_ingest": st.get("last_ingest", {}),
                    }
                    print(json.dumps(payload, ensure_ascii=False, indent=2))
                    return True
                if name == "host":
                    st = load_state()
                    devices = st.get("devices", []) if isinstance(st.get("devices"), list) else []
                    current = st.get("current_host", "")
                    if not args or args[0].lower() == "list":
                        if not devices:
                            print("martin: No paired devices.")
                            return True
                        for dev in devices:
                            marker = "*" if dev.get("name") == current else " "
                            print(f"{marker} {dev.get('name','')} ({dev.get('paired_at','')})")
                        return True
                    action = args[0].lower()
                    if action == "pair":
                        name = " ".join(args[1:]).strip()
                        if not name:
                            print("martin: Use /host pair <name>.")
                            return True
                        if any(d.get("name") == name for d in devices):
                            print("martin: Device already paired.")
                            return True
                        device = {"name": name, "paired_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
                        devices.append(device)
                        st["devices"] = devices
                        st["current_host"] = name
                        save_state(st)
                        print(f"martin: Paired {name}.")
                        return True
                    if action == "use":
                        name = " ".join(args[1:]).strip()
                        if not name:
                            print("martin: Use /host use <name>.")
                            return True
                        if not any(d.get("name") == name for d in devices):
                            print("martin: Device not found.")
                            return True
                        st["current_host"] = name
                        save_state(st)
                        print(f"martin: Active host set to {name}.")
                        return True
                    print("martin: Use /host list|pair <name>|use <name>.")
                    return True
                if name == "remote":
                    if not args:
                        print("martin: Use /remote start|stop|status|config.")
                        return True
                    action = args[0].lower()
                    if action == "start":
                        v = validate_transport(cfg)
                        if not v.get("ok"):
                            print(f"martin: Remote transport missing {', '.join(v.get('missing', []))}.")
                            return True
                        resp = start_tunnel(cfg)
                        if resp.get("ok"):
                            print(f"martin: Remote tunnel started (pid {resp.get('pid')}).")
                        else:
                            print(f"martin: Remote tunnel start failed ({resp.get('error')}).")
                        return True
                    if action == "stop":
                        resp = stop_tunnel(cfg)
                        if resp.get("ok"):
                            print("martin: Remote tunnel stopped.")
                        else:
                            print(f"martin: Remote tunnel stop failed ({resp.get('error')}).")
                        return True
                    if action == "status":
                        resp = status_tunnel(cfg)
                        resp["validation"] = validate_transport(cfg)
                        print(json.dumps(resp, ensure_ascii=False, indent=2))
                        return True
                    if action == "config":
                        st = load_state()
                        overrides = st.get("remote_transport_overrides", {}) if isinstance(st, dict) else {}
                        if len(args) == 1 or args[1].lower() == "show":
                            print(json.dumps({"overrides": overrides}, ensure_ascii=False, indent=2))
                            return True
                        if args[1].lower() == "set":
                            if len(args) < 4:
                                print("martin: Use /remote config set <key> <value>.")
                                return True
                            key = args[2].strip()
                            val = " ".join(args[3:]).strip()
                            current = st.get("current_host", "local")
                            if not current:
                                current = "local"
                            overrides = overrides if isinstance(overrides, dict) else {}
                            host_cfg = overrides.get(current, {}) or {}
                            host_cfg[key] = val
                            overrides[current] = host_cfg
                            st["remote_transport_overrides"] = overrides
                            save_state(st)
                            print(f"martin: Remote config set for {current}.")
                            return True
                        print("martin: Use /remote config show|set <key> <value>.")
                        return True
                    print("martin: Use /remote start|stop|status|config.")
                    return True
                if name == "redaction":
                    if not args or args[0].lower() != "report":
                        print("martin: Use /redaction report [days].")
                        return True
                    days = 30
                    if len(args) > 1:
                        try:
                            days = int(args[1])
                        except Exception:
                            days = 30
                    cutoff = time.time() - (days * 86400)
                    total = 0
                    redacted = 0
                    try:
                        if LEDGER_FILE.exists():
                            with open(LEDGER_FILE, "r", encoding="utf-8") as f:
                                for line in f:
                                    try:
                                        row = json.loads(line)
                                        entry = row.get("entry", {})
                                        ts = entry.get("ts", "")
                                        if ts:
                                            dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                                            if dt.timestamp() < cutoff:
                                                continue
                                        total += 1
                                        data = entry.get("data", {}) or {}
                                        if data.get("redacted") or data.get("sanitized"):
                                            redacted += 1
                                    except Exception:
                                        continue
                    except Exception:
                        pass
                    report = {"window_days": days, "entries": total, "redacted_entries": redacted}
                    print(json.dumps(report, ensure_ascii=False, indent=2))
                    return True
                if name == "trust":
                    if not args or args[0].lower() != "keygen":
                        print("martin: Use /trust keygen.")
                        return True
                    try:
                        from researcher.crypto_utils import generate_key
                        key = generate_key()
                        print("martin: New encryption key:")
                        print(key)
                    except Exception as e:
                        print(f"martin: Keygen failed ({e})")
                    return True
                if name == "encrypt":
                    if not args:
                        print("martin: Use /encrypt <path>.")
                        return True
                    path = Path(" ".join(args)).expanduser()
                    key_env = (cfg.get("trust_policy", {}) or {}).get("encryption_key_env", "MARTIN_ENCRYPTION_KEY")
                    key = os.environ.get(key_env or "")
                    if not key:
                        print("martin: Encryption key not set; set env first.")
                        return True
                    try:
                        from researcher.crypto_utils import encrypt_text
                        raw = path.read_text(encoding="utf-8")
                        enc = encrypt_text(raw, key)
                        out_path = path.with_suffix(path.suffix + ".enc")
                        if preview_write(out_path, enc):
                            out_path.write_text(enc, encoding="utf-8")
                            print(f"martin: Encrypted to {out_path}")
                    except Exception as e:
                        print(f"martin: Encrypt failed ({e})")
                    return True
                if name == "decrypt":
                    if not args:
                        print("martin: Use /decrypt <path>.")
                        return True
                    path = Path(" ".join(args)).expanduser()
                    key_env = (cfg.get("trust_policy", {}) or {}).get("encryption_key_env", "MARTIN_ENCRYPTION_KEY")
                    key = os.environ.get(key_env or "")
                    if not key:
                        print("martin: Encryption key not set; set env first.")
                        return True
                    try:
                        from researcher.crypto_utils import decrypt_text
                        raw = path.read_text(encoding="utf-8")
                        dec = decrypt_text(raw, key)
                        out_path = path.with_suffix(".dec")
                        if preview_write(out_path, dec):
                            out_path.write_text(dec, encoding="utf-8")
                            print(f"martin: Decrypted to {out_path}")
                    except Exception as e:
                        print(f"martin: Decrypt failed ({e})")
                    return True
                if name == "rotate":
                    if len(args) < 3:
                        print("martin: Use /rotate <path> <old_env> <new_env>.")
                        return True
                    path = Path(args[0]).expanduser()
                    old_env = args[1]
                    new_env = args[2]
                    old_key = os.environ.get(old_env or "")
                    new_key = os.environ.get(new_env or "")
                    if not old_key or not new_key:
                        print("martin: Missing old/new keys in env.")
                        return True
                    try:
                        from researcher.crypto_utils import decrypt_text, encrypt_text
                        raw = path.read_text(encoding="utf-8")
                        dec = decrypt_text(raw, old_key)
                        enc = encrypt_text(dec, new_key)
                        out_path = path.with_suffix(path.suffix + ".rotated")
                        if preview_write(out_path, enc):
                            out_path.write_text(enc, encoding="utf-8")
                            print(f"martin: Rotated key output {out_path}")
                    except Exception as e:
                        print(f"martin: Rotate failed ({e})")
                    return True
                if name == "export":
                    if not args:
                        print("martin: Use /export session <path>.")
                        return True
                    if args[0].lower() != "session":
                        print("martin: Use /export session <path>.")
                        return True
                    if _privacy_enabled():
                        print("martin: Privacy mode is on; session export is disabled.")
                        return True
                    out_path = args[1] if len(args) > 1 else str(Path("logs") / "session_export.json")
                    st = load_state()
                    bundle = {
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "transcript_tail": st.get("resume_snapshot", {}).get("transcript_tail", []),
                        "context_cache": st.get("context_cache", {}),
                        "tasks": st.get("tasks", []),
                        "tool_ledger": read_recent(limit=50),
                    }
                    try:
                        content = json.dumps(bundle, ensure_ascii=False, indent=2) + "\n"
                        try:
                            st = load_state()
                            current_host = st.get("current_host", "") if isinstance(st, dict) else ""
                        except Exception:
                            current_host = ""
                        enc_cfg = _encryption_policy(cfg, current_host)
                        if enc_cfg.get("encrypt"):
                            from researcher.crypto_utils import encrypt_text
                            key_env = enc_cfg.get("key_env")
                            key = os.environ.get(key_env or "")
                            if not key:
                                print("martin: Encryption key not set; export blocked.")
                                return True
                            content = encrypt_text(content, key)
                            out_path = out_path + ".enc" if not out_path.endswith(".enc") else out_path
                        if preview_write(Path(out_path), content):
                            Path(out_path).write_text(content, encoding="utf-8")
                            print(f"martin: Exported session to {out_path}")
                        else:
                            print("martin: Export cancelled.")
                    except Exception as e:
                        print(f"martin: Export failed ({e})")
                    return True
                if name == "import":
                    if not args:
                        print("martin: Use /import session <path>.")
                        return True
                    if args[0].lower() != "session":
                        print("martin: Use /import session <path>.")
                        return True
                    in_path = args[1] if len(args) > 1 else ""
                    if not in_path:
                        print("martin: Use /import session <path>.")
                        return True
                    try:
                        content = Path(in_path).read_text(encoding="utf-8")
                        bundle = json.loads(content)
                    except Exception as e:
                        print(f"martin: Import failed ({e})")
                        return True
                    try:
                        st = load_state()
                        st["context_cache"] = bundle.get("context_cache", {}) or {}
                        st["tasks"] = bundle.get("tasks", []) or []
                        st["resume_snapshot"] = {
                            "ts": bundle.get("ts", ""),
                            "context_cache": bundle.get("context_cache", {}) or {},
                            "transcript_tail": bundle.get("transcript_tail", []) or [],
                        }
                        save_state(st)
                        print("martin: Session import complete.")
                    except Exception as e:
                        print(f"martin: Import failed ({e})")
                    return True
                if name == "librarian":
                    if not args:
                        print("martin: Use /librarian inbox|request <topic>|sources <topic>|accept <n>|dismiss <n>.")
                        return True
                    action = args[0].lower()
                    if action == "inbox":
                        st = load_state()
                        inbox = st.get("librarian_inbox", [])
                        if not inbox:
                            print("martin: Librarian inbox is empty.")
                            return True
                        for idx, item in enumerate(inbox[-10:], 1):
                            msg = item.get("message", {})
                            event = msg.get("event", "note")
                            details = msg.get("details", {})
                            topic = details.get("topic") or details.get("prompt") or ""
                            note_id = details.get("note_id", "")
                            ingestable = "summary" in details
                            flag = "[ingestable]" if ingestable else ""
                            trust = details.get("trust_score")
                            stale = details.get("stale")
                            trust_txt = f"trust={trust:.2f}" if isinstance(trust, (int, float)) else ""
                            stale_txt = "stale" if stale else ""
                            extras = " ".join(p for p in [trust_txt, stale_txt] if p)
                            line = f"{idx}. {item.get('ts','')}: {event} {topic} {note_id} {flag} {extras}".strip()
                            print(line)
                            if event == "rag_gap" and details.get("suggestion"):
                                print(f"   suggestion: {details.get('suggestion')}")
                        return True
                    if action == "request":
                        topic = " ".join(args[1:]).strip()
                        if not topic:
                            print("martin: Provide a topic to request.")
                            return True
                        client = LibrarianClient()
                        resp = client.request_research(topic)
                        print(json.dumps(resp, ensure_ascii=False, indent=2))
                        log_event(load_state(), "librarian_request", topic=topic, status=resp.get("status"))
                        return True
                    if action == "sources":
                        topic = " ".join(args[1:]).strip()
                        if not topic:
                            print("martin: Provide a topic to request sources.")
                            return True
                        client = LibrarianClient()
                        resp = client.request_sources(topic)
                        print(json.dumps(resp, ensure_ascii=False, indent=2))
                        log_event(load_state(), "librarian_sources_request", topic=topic, status=resp.get("status"))
                        return True
                    if action == "accept":
                        if len(args) < 2:
                            print("martin: Provide an inbox index to accept.")
                            return True
                        try:
                            idx = int(args[1])
                        except ValueError:
                            print("martin: Invalid index.")
                            return True
                        st = load_state()
                        inbox = st.get("librarian_inbox", [])
                        if not inbox:
                            print("martin: Librarian inbox is empty.")
                            return True
                        window = inbox[-10:]
                        item = window[idx - 1] if 1 <= idx <= min(10, len(window)) else None
                        if not item:
                            print("martin: Index out of range.")
                            return True
                        details = (item.get("message") or {}).get("details", {})
                        summary = details.get("summary", "")
                        sources_text = details.get("sources_text", "")
                        topic = details.get("topic") or details.get("prompt") or "librarian_note"
                        client = LibrarianClient()
                        if sources_text:
                            resp = client.ingest_text(sources_text, topic=topic, source="librarian_sources")
                            print(json.dumps(resp, ensure_ascii=False, indent=2))
                            log_event(load_state(), "librarian_ingest_sources", topic=topic, status=resp.get("status"))
                        elif not summary:
                            resp = client.request_research(topic)
                            print(json.dumps(resp, ensure_ascii=False, indent=2))
                            log_event(load_state(), "librarian_request_from_gap", topic=topic, status=resp.get("status"))
                        else:
                            resp = client.ingest_text(summary, topic=topic, source="librarian_note")
                            print(json.dumps(resp, ensure_ascii=False, indent=2))
                            log_event(load_state(), "librarian_ingest_text", topic=topic, status=resp.get("status"))
                        if resp.get("status") == "success":
                            st["librarian_inbox"] = [i for i in inbox if i is not item]
                            save_state(st)
                        return True
                    if action == "dismiss":
                        if len(args) < 2:
                            print("martin: Provide an inbox index to dismiss.")
                            return True
                        try:
                            idx = int(args[1])
                        except ValueError:
                            print("martin: Invalid index.")
                            return True
                        st = load_state()
                        inbox = st.get("librarian_inbox", [])
                        window = inbox[-10:]
                        item = window[idx - 1] if 1 <= idx <= min(10, len(window)) else None
                        if not item:
                            print("martin: Index out of range.")
                            return True
                        st["librarian_inbox"] = [i for i in inbox if i is not item]
                        save_state(st)
                        print("martin: Dismissed.")
                        return True
                    print("martin: Unknown /librarian action.")
                    return True
                if name == "catalog":
                    print("martin: Fetching card catalog from Librarian...")
                    # Use the same dispatcher as the main loop
                    ok, output = dispatch_internal_ability("catalog.list", "")
                    if ok:
                        print(output)
                    else:
                        print(f"martin: Error fetching catalog: {output}")
                    return True
                if name == "context":
                    if args and args[0].lower() == "refresh":
                        from researcher.context_harvest import gather_context
                        fast_ctx = not (Path.cwd() / ".git").exists()
                        context_cache = gather_context(Path.cwd(), max_recent=int(cfg.get("context", {}).get("max_recent", 10)), fast=fast_ctx)
                        st = load_state()
                        st["context_cache"] = context_cache
                        if behavior_flags.get("context_block"):
                            st["active_context"] = _build_active_context(st)
                        save_state(st)
                        chat_ui.print_context_summary(context_cache)
                        return True
                    if not context_cache:
                        from researcher.context_harvest import gather_context
                        fast_ctx = not (Path.cwd() / ".git").exists()
                        context_cache = gather_context(Path.cwd(), max_recent=int(cfg.get("context", {}).get("max_recent", 10)), fast=fast_ctx)
                        st = load_state()
                        st["context_cache"] = context_cache
                        save_state(st)
                    payload = dict(context_cache)
                    try:
                        st = load_state()
                        prev = st.get("resume_snapshot", {}).get("context_cache", {})
                        if isinstance(prev, dict):
                            prev_recent = set(prev.get("recent_files", []) or [])
                            curr_recent = set(payload.get("recent_files", []) or [])
                            payload["context_diff"] = {
                                "new_recent_files": sorted(list(curr_recent - prev_recent))[:20]
                            }
                        if behavior_flags.get("context_block"):
                            payload["active_context"] = _build_active_context(st)
                    except Exception:
                        pass
                    print(json.dumps(payload, ensure_ascii=False, indent=2))
                    return True
                if name == "goal":
                    st = load_state()
                    if not args or args[0].lower() == "status":
                        print(json.dumps({"active_goal": st.get("active_goal", "")}, ensure_ascii=False, indent=2))
                        return True
                    action = args[0].lower()
                    if action == "set":
                        text = " ".join(args[1:]).strip()
                        if not text:
                            print("martin: Use /goal set <text>.")
                            return True
                        st["active_goal"] = text
                        save_state(st)
                        print("martin: Goal updated.")
                        return True
                    if action == "clear":
                        st["active_goal"] = ""
                        save_state(st)
                        print("martin: Goal cleared.")
                        return True
                    print("martin: Use /goal status|set <text>|clear.")
                    return True
                if name == "agent":
                    if not args:
                        print(f"martin: agent_mode={'on' if agent_mode else 'off'}")
                        return True
                    if args[0].lower() == "on":
                        agent_mode = True
                        print("martin: Agent mode ON.")
                        return True
                    if args[0].lower() == "off":
                        agent_mode = False
                        print("martin: Agent mode OFF.")
                        return True
                    if args[0].lower() == "status":
                        print(f"martin: agent_mode={'on' if agent_mode else 'off'}")
                        return True
                if name == "cloud":
                    if cfg.get("local_only"):
                        print("martin: Cloud is disabled by local-only mode.")
                        return True
                    if not args:
                        print(f"martin: cloud={'on' if cloud_enabled else 'off'}")
                        return True
                    if args[0].lower() == "on":
                        cloud_enabled = True
                        print("martin: Cloud ON.")
                        return True
                    if args[0].lower() == "off":
                        cloud_enabled = False
                        print("martin: Cloud OFF.")
                        return True
                if name == "ask":
                    prompt = " ".join(args).strip()
                    if not prompt:
                        print("martin: Provide a question.")
                        return True
                    cmd_ask(cfg, prompt, k=5, use_llm=False, cloud_mode="off", force_simple=False, as_json=False)
                    return True
                if name == "ingest":
                    if not args:
                        print("martin: Provide a path to ingest.")
                        return True
                    text = " ".join(args).lower()
                    ctx = get_system_context()
                    base = ""
                    if "onedrive" in text and "desktop" in text:
                        base = ctx.get("paths", {}).get("onedrive_desktop") or ""
                    elif "desktop" in text:
                        base = ctx.get("paths", {}).get("desktop") or ""
                    if base and ("research agent" in text or "research_agent" in text):
                        target = str(Path(base) / "research_agent")
                        cmd_ingest(cfg, [target], force_simple=False, as_json=False, skip_librarian=True)
                        return True
                    if base:
                        cmd_ingest(cfg, [base], force_simple=False, as_json=False, skip_librarian=True)
                        return True
                    cmd_ingest(cfg, args, force_simple=False, as_json=False, skip_librarian=True)
                    return True
                print("martin: Unknown command. Use /help.")
                return True

            if user_input.strip().startswith("/"):
                if _handle_slash(user_input.strip()):
                    if should_exit:
                        break
                    continue

            auto_paths = _extract_paths_from_text(user_input)
            if not auto_paths:
                auto_paths = _extract_desktop_targets(user_input)
            if auto_paths:
                cmd_ingest(cfg, auto_paths, force_simple=False, as_json=False, skip_librarian=False)
            elif "desktop" in user_input.lower() and ("read" in user_input.lower() or "ingest" in user_input.lower()):
                print("martin: I can ingest a file from your Desktop. Please tell me the filename or paste the full path.")

            if cloud_enabled and cfg.get("cloud", {}).get("trigger_on_disagreement") and _is_disagreement(user_input) and not cfg.get("local_only"):
                prompt = (last_user_request or user_input).strip()
                prompt = f"{prompt}\n\nUser feedback: {user_input}\nPlease answer correctly."
                client = LibrarianClient()
                allow_cloud, sanitized_prompt = _confirm_cloud_send(prompt or "", approval_policy, agent_mode=agent_mode, as_json=False)
                if allow_cloud:
                    cloud_resp = client.query_cloud(
                        prompt=sanitized_prompt,
                        cloud_mode="always",
                        cloud_cmd=cfg.get("cloud", {}).get("cmd_template") or os.environ.get("CLOUD_CMD", ""),
                    )
                else:
                    cloud_resp = {"status": "error", "message": "user_denied"}
                client.close()
                if cloud_resp.get("status") == "success":
                    result = cloud_resp.get("result", {})
                    output = result.get("output", "")
                    if output:
                        print(f"\033[92mmartin:\n{output}\033[0m")
                        transcript.append("martin: " + output)
                        interaction_history.append("martin: " + output)
                        continue

            interaction_history.append("You: " + user_input)
            transcript.append("You: " + user_input)
            session_transcript.append("You: " + user_input)
            try:
                if not _privacy_enabled():
                    st = load_state()
                    st["session_memory"] = {"transcript": session_transcript[-200:]}
                    save_state(st)
            except Exception:
                pass
            if not _is_disagreement(user_input):
                last_user_request = user_input

            turn_bar = tqdm(total=2, desc="Turn", unit="step", leave=False) if True else None # Always show for now

            if turn_bar: turn_bar.update(1)
            step_details = decide_next_step(user_input)
            plan_queue: List[Dict[str, Any]] = []
            if step_details.get("behavior") == "plan":
                try:
                    plan_queue = _plan_action_queue(user_input, context_cache)
                    if plan_queue:
                        st = load_state()
                        st["action_queue"] = plan_queue
                        st["action_queue_ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                        save_state(st)
                        log_event(st, "action_queue", count=len(plan_queue))
                        if len(plan_queue) > 3:
                            _render_action_queue(plan_queue)
                except Exception:
                    plan_queue = []
            try:
                intent_raw = step_details.get("user_intent_summary", "") or ""
                intent_sanitized, changed = sanitize.sanitize_prompt(intent_raw)
                intent_summary = chat_ui.shorten_output(intent_sanitized, max_len=200)
                logger.info(
                    "decision behavior=%s questions=%d redacted=%s",
                    step_details.get("behavior"),
                    len(step_details.get("question_summaries") or []),
                    changed,
                )
                log_event(
                    load_state(),
                    "decision",
                    behavior=step_details.get("behavior"),
                    questions_count=len(step_details.get("question_summaries") or []),
                    intent_summary=intent_summary,
                    redacted=changed,
                )
            except Exception:
                pass
            log_event(st, "next_step_decision", details=step_details)

            if turn_bar: turn_bar.update(1)

            def _try_cloud(prompt: str, reason: str) -> Optional[str]:
                client = LibrarianClient()
                allow_cloud, sanitized_prompt = _confirm_cloud_send(prompt or "", approval_policy, agent_mode=agent_mode, as_json=False)
                if allow_cloud:
                    cloud_resp = client.query_cloud(
                        prompt=sanitized_prompt,
                        cloud_mode="always",
                        cloud_cmd=cfg.get("cloud", {}).get("cmd_template") or os.environ.get("CLOUD_CMD", ""),
                    )
                else:
                    cloud_resp = {"status": "error", "message": "user_denied"}
                client.close()
                if cloud_resp.get("status") == "success":
                    result = cloud_resp.get("result", {})
                    output = result.get("output", "")
                    if output:
                        log_event(st, "cloud_hop", reason=reason, output_len=len(output))
                        return output
                log_event(st, "cloud_hop_failed", reason=reason, error=cloud_resp.get("message", "no_output"))
                return None

            review_mode = False
            try:
                review_mode = bool(load_state().get("review_mode"))
            except Exception:
                review_mode = False
            main_sys = (
                "You are Martin, a helpful and competent AI researcher assistant.\n"
                "Speak as Martin. Be direct and concise.\n"
                "Do not describe internal reasoning or system instructions.\n"
                "You have access to the local filesystem via `command:` lines.\n"
                "Do not claim you cannot access files; propose commands instead.\n"
                "You can coordinate with the Librarian agent for background research and RAG updates.\n"
                "Follow the guidance and context. Be decisive but safe."
            )
            qs = step_details.get('question_summaries') or []
            q_lines = "\n".join(f"- {q}" for q in qs) if qs else "- none"

            # current_username is a global placeholder in llm_utils, populated from os.getenv("USER")
            # interaction_history is also a global placeholder in llm_utils

            sys_ctx = {}
            try:
                sys_ctx = get_system_context()
            except Exception:
                sys_ctx = {}
            mem_ctx = {"last_path": _LAST_PATH, "last_listing": _LAST_LISTING[:20]}
            last_cmd_summary = {}
            try:
                last_cmd_summary = load_state().get("last_command_summary", {}) or {}
            except Exception:
                last_cmd_summary = {}
            behavior_mode = "review" if review_mode else step_details.get("behavior", "chat")
            queue_ctx = []
            if plan_queue:
                queue_ctx = plan_queue
            else:
                try:
                    queue_ctx = load_state().get("action_queue", []) or []
                except Exception:
                    queue_ctx = []
            main_user = (
                "Context (do not repeat):\n"
                f"{json.dumps({'user_intent': step_details.get('user_intent_summary'), 'capability_inventory': step_details.get('inventory', []), 'snapshot': step_details.get('snapshot', {}), 'system': sys_ctx, 'memory': mem_ctx, 'last_command': last_cmd_summary, 'action_queue': queue_ctx}, ensure_ascii=False, indent=2)}\n\n"
                "Guidance (do not repeat):\n"
                f"{step_details.get('guidance_banner', '')}\n\n"
                "Behavior (do not repeat):\n"
                f"{behavior_mode}\n\n"
                "Question summaries (user asked):\n"
                f"{q_lines}\n\n"
                "Internal invocation protocol: command: martin.<ability_key> <payload>\n\n"
                "CRITICAL: For any request involving file system navigation (cd), listing files (ls, dir), reading files (cat, type), or running tools, you MUST reply with a `command:` line. Do not suggest the command in plain text.\n"
                "When you decide to execute an action, adopt a helpful and proactive tone, like 'Let me handle that for you,' before providing the `command:` line.\n"
                "If the user asks to inspect files, run tools, or check system state, include `command:` lines.\n"
                "If behavior = chat, respond directly to the user with a helpful reply, but follow the CRITICAL rule above.\n"
                "If behavior = plan, produce an ordered checklist with time cues and a check-in cadence; confirm you will track progress and keep it concise.\n"
                "If behavior = build/run/diagnose, output precise steps only if truly warranted.\n"
                "If behavior = review, focus on bugs, risks, regressions, and missing tests; be concise and specific.\n"
                "If behavior = review, format output with sections: Findings, Questions, Tests. Use bullets under Findings.\n"
                "If behavior = review and the user says 'this repo' without a path, assume the current workspace is the target.\n"
                "Clarification policy: ask only when blocked; otherwise proceed and state your assumptions.\n"
                "Cadence: progress note -> actions -> results (keep it tight).\n"
                "Do not mention internal analysis, guidance, or behavior classification."
            )
            payload = {
                "model": MODEL_MAIN,
                "input": [
                    {"role": "system", "content": main_sys},
                    {"role": "user", "content": main_user},
                ],
                "temperature": 0.4,
                "max_output_tokens": 1200,
            }
            stage_label = "thinking" if behavior_mode == "chat" else f"{behavior_mode} plan"
            stop_event = threading.Event()
            label_state = {"label": stage_label}
            _work_spinner(stage_label, stop_event, label_state)
            def _progress_cb(msg: str) -> None:
                label_state["label"] = f"{stage_label} · {msg}"
            bot_json = _post_responses(payload, label="Main", progress_cb=_progress_cb) # Use llm_utils's post_responses
            stop_event.set()
            bot_response = _extract_output_text(bot_json) or ""
            interaction_history.append("martin: " + bot_response)
            if verbose_logging:
                try:
                    summary, redacted = _summarize_text(bot_response, max_len=240)
                    if summary:
                        logger.info("assistant_output summary=%s redacted=%s", summary, redacted)
                    log_event(load_state(), "assistant_output", summary=summary, redacted=redacted)
                except Exception:
                    pass

            if turn_bar: turn_bar.update(1)
            if turn_bar: turn_bar.close()

            if not bot_response:
                print("\033[93mmartin: No response received from main call.\033[0m")
                logger.info("chat_no_response")
                continue

            if cfg.get("rephraser", {}).get("enabled") and "command:" not in bot_response.lower():
                bot_response = rephraser(bot_response)

            if cloud_enabled and cfg.get("cloud", {}).get("trigger_on_empty_or_decline", True) and not cfg.get("local_only"):
                lowered = (bot_response or "").lower()
                decline = any(p in lowered for p in ("i can't", "i cannot", "unable to", "can't directly", "i don't know"))
                if decline:
                    cloud_answer = _try_cloud(user_input, "assistant_declined")
                    if cloud_answer:
                        bot_response = cloud_answer

            def _auto_command_for_request(user_text: str, reply: str) -> str:
                global _LAST_PATH
                text = (user_text or "").lower()
                if "command:" in reply.lower():
                    return reply

                def _quote_path(p: str) -> str:
                    if not p:
                        return p
                    if p.startswith('"') and p.endswith('"'):
                        return p
                    if " " in p or "(" in p or ")" in p:
                        return f"\"{p}\""
                    return p

                def _best_listing_match(text_l: str) -> str:
                    best = ""
                    best_score = 0
                    tokens = [t for t in text_l.split() if t]
                    for name in _LAST_LISTING:
                        n = name.lower()
                        if not n:
                            continue
                        score = 0
                        if n in text_l:
                            score += len(n) * 2
                        for t in tokens:
                            if t in n:
                                score += len(t)
                        if score > best_score:
                            best_score = score
                            best = name
                    return best

                if "memory" in text or "what do you remember" in text or "check memory" in text:
                    mem = {
                        "last_path": _LAST_PATH,
                        "last_listing": _LAST_LISTING[:20],
                    }
                    return "Memory:\n" + json.dumps(mem, ensure_ascii=False)

                if _LAST_PATH and any(k in text for k in ("navigate", "open", "look at", "list", "show", "read", "inspect")):
                    best = _best_listing_match(text)
                    if best:
                        target = str(Path(_LAST_PATH) / best)
                        if os.name == "nt":
                            cmd = f"command: Get-ChildItem -Path {_quote_path(target)} -Force"
                        else:
                            cmd = f"command: ls -la {_quote_path(target)}"
                        return f"I can open {best} now.\n\n{cmd}"

                if "desktop" in text:
                    if os.name == "nt":
                        if "onedrive" in text:
                            cmd = "command: Get-ChildItem -Path $env:USERPROFILE\\OneDrive\\Desktop -Force"
                        else:
                            cmd = "command: Get-ChildItem -Path $env:USERPROFILE\\Desktop -Force"
                    else:
                        cmd = "command: ls -la ~/Desktop"
                    return "I can list your desktop now.\n\n" + cmd

                if _LAST_PATH and ("open work" in text or "open tasks" in text or "todo" in text):
                    target = _LAST_PATH
                    best = _best_listing_match(text)
                    if best:
                        target = str(Path(_LAST_PATH) / best)
                    cmd = f"command: rg -n \"TODO|FIXME|TBD|pending\" {_quote_path(target)}"
                    return "Checking for open work in the last folder.\n\n" + cmd

                return reply

            bot_response = _auto_command_for_request(user_input, bot_response)
            if review_mode:
                bot_response = _format_review_response(bot_response)

            print(f"\033[92mmartin:\n{bot_response}\033[0m")
            transcript.append("martin: " + bot_response)
            session_transcript.append("martin: " + bot_response)
            try:
                if not _privacy_enabled():
                    st = load_state()
                    st["session_memory"] = {"transcript": session_transcript[-200:]}
                    if behavior_flags.get("summaries"):
                        summary, _ = _summarize_text(bot_response, max_len=160)
                        if summary:
                            st["last_action_summary"] = f"responded: {summary}"
                    if behavior_flags.get("context_block"):
                        st["active_context"] = _build_active_context(st)
                    save_state(st)
            except Exception:
                pass
            if behavior_flags.get("summaries"):
                try:
                    st = load_state()
                    summary = st.get("last_action_summary", "")
                    next_action = ""
                    tasks = st.get("tasks", []) if isinstance(st.get("tasks"), list) else []
                    if tasks:
                        next_action = tasks[0].get("text", "")
                    if summary:
                        line = f"martin: Summary: {summary}"
                        if next_action:
                            line += f" | Next: {next_action}"
                        print(line)
                except Exception:
                    pass
            if ui_flags.get("footer"):
                _render_footer()

            def _parse_internal_cmd(c: str) -> Tuple[Optional[str], Optional[str]]:
                s = c.strip()
                # Martin's internal commands start with "martin."
                if not s.lower().startswith("martin."):
                    return (None, None)
                body = s[len("martin."):].strip()
                if " " in body:
                    key, payload = body.split(" ", 1)
                elif ":" in body:
                    key, payload = body.split(":", 1)
                else:
                    key, payload = body, ""
                return (key.strip(), payload.strip())

            terminal_commands = extract_commands(bot_response) if "command:" in bot_response.lower() else [] # Use researcher's extract_commands
            intent_raw = step_details.get("user_intent_summary", "") or ""
            intent_summary = intent_raw
            changed = False
            try:
                intent_sanitized, changed = sanitize.sanitize_prompt(intent_raw)
                intent_summary = chat_ui.shorten_output(intent_sanitized, max_len=200)
            except Exception:
                pass
            questions_count = len(step_details.get("question_summaries") or [])
            action_taken = "plan" if terminal_commands else "response"
            outcome = "plan_proposed" if terminal_commands else "response_only"
            followup_needed = questions_count > 0
            try:
                log_event(
                    load_state(),
                    "request_audit",
                    intent_summary=intent_summary,
                    behavior=behavior_mode,
                    action_taken=action_taken,
                    outcome=outcome,
                    questions_count=questions_count,
                    followup_needed=followup_needed,
                    redacted=changed,
                )
            except Exception:
                pass
            try:
                logger.info(
                    "request_audit behavior=%s action=%s outcome=%s questions=%d followup=%s redacted=%s",
                    behavior_mode,
                    action_taken,
                    outcome,
                    questions_count,
                    followup_needed,
                    changed,
                )
            except Exception:
                pass
            if terminal_commands:
                rationale_text = user_input.strip() or "requested action"
                print("\033[96mmartin: Rationale:\033[0m " + rationale_text)
                try:
                    summary, redacted = _summarize_text(rationale_text, max_len=160)
                    st = load_state()
                    st["last_plan_rationale"] = summary
                    save_state(st)
                    log_event(load_state(), "plan_rationale", summary=summary, redacted=redacted)
                except Exception:
                    pass
                print("\n\033[96mmartin: Proposed command plan (review):\033[0m")
                risk_info = []
                for c in terminal_commands:
                    risk = classify_command_risk(c, command_allowlist, command_denylist)
                    risk_info.append(risk)
                if verbose_logging:
                    try:
                        sanitized_cmds, redacted = _sanitize_command_list(terminal_commands)
                        log_event(load_state(), "plan_proposed", count=len(terminal_commands), cmds=sanitized_cmds, redacted=redacted)
                        logger.info("plan_proposed count=%d redacted=%s", len(terminal_commands), redacted)
                    except Exception:
                        pass
                for i, (c, risk) in enumerate(zip(terminal_commands, risk_info), 1):
                    tag = "" if risk["level"] == "low" else f" [{risk['level'].upper()}]"
                    print(f"  {i}. {c}{tag}")
                    if risk["reasons"]:
                        print(f"     - {', '.join(risk['reasons'])}")
                blocked = [c for c, r in zip(terminal_commands, risk_info) if r["level"] == "blocked"]
                if blocked:
                    print("\033[93mmartin: One or more commands were blocked by policy and will be skipped.\033[0m")
                    terminal_commands = [c for c, r in zip(terminal_commands, risk_info) if r["level"] != "blocked"]
                    risk_info = [r for r in risk_info if r["level"] != "blocked"]
                def _edit_commands(cmds: List[str]) -> List[str]:
                    edited = []
                    for idx, c in enumerate(cmds, 1):
                        try:
                            repl = input(f"\033[93mEdit command {idx} (enter to keep):\033[0m ").strip()
                        except (EOFError, KeyboardInterrupt):
                            repl = ""
                        edited.append(repl if repl else c)
                    return edited
                if approval_policy in ("never", "on-failure") or agent_mode:
                    confirm = "yes"
                else:
                    while True:
                        try:
                            confirm = input("\033[93mApprove running these commands? (yes/no/abort/edit/inline/editor/explain/dry-run)\033[0m ").strip().lower()
                        except (EOFError, KeyboardInterrupt):
                            confirm = "no"
                        if confirm == "explain":
                            print("\033[96mmartin: Rationale:\033[0m " + (user_input.strip() or "requested action"))
                            continue
                        if confirm == "edit":
                            terminal_commands = _edit_commands(terminal_commands)
                            continue
                        if confirm == "inline":
                            terminal_commands = edit_commands_inline(terminal_commands)
                            continue
                        if confirm == "editor":
                            terminal_commands = edit_commands_in_editor(terminal_commands)
                            continue
                        if confirm == "dry-run":
                            print("\033[92mmartin: Dry run only; no commands executed.\033[0m")
                            logger.info("chat_cmd_dry_run count=%d", len(terminal_commands))
                            terminal_commands = []
                            break
                        break
                if confirm == "abort":
                    print("\033[92mmartin: Aborting per request.\033[0m")
                    logger.info("chat_cmd_abort count=%d", len(terminal_commands))
                    continue
                elif confirm == "no":
                    print("\033[92mmartin: Understood - not running commands. I remain at your disposal.\033[0m")
                    logger.info("chat_cmd_denied count=%d", len(terminal_commands))
                    continue
                _auto_context_surface("before plan run")
                if any(r["level"] == "high" for r in risk_info):
                    try:
                        high_confirm = input("\033[91mHigh-risk commands detected. Type YES to proceed:\033[0m ").strip().lower()
                    except (EOFError, KeyboardInterrupt):
                        high_confirm = "no"
                    if high_confirm != "yes":
                        print("\033[93mmartin: High-risk commands skipped.\033[0m")
                        terminal_commands = [c for c, r in zip(terminal_commands, risk_info) if r["level"] != "high"]
                        risk_info = [r for r in risk_info if r["level"] != "high"]

            if not terminal_commands:
                continue

            # Persist plan state for UX continuity
            try:
                st = load_state()
                st["last_plan"] = {"steps": terminal_commands, "status": "pending"}
                _maybe_set_plan_tasks(st, terminal_commands)
                save_state(st)
            except Exception:
                pass

            plan = []
            for i, cmd in enumerate(terminal_commands):
                raw = cmd.replace("command:", "", 1).strip() if cmd.lower().startswith("command:") else cmd
                ability_key, payload_txt = _parse_internal_cmd(raw)
                risk = classify_command_risk(cmd, command_allowlist, command_denylist)
                plan.append({
                    "index": i + 1,
                    "cmd": cmd,
                    "status": "pending",
                    "internal_key": ability_key,
                    "payload": payload_txt,
                    "risk": risk.get("level"),
                    "risk_reasons": risk.get("reasons", []),
                    "rc": None,
                    "stdout": "",
                    "stderr": "",
                    "output": "",
                    "started_at": None,
                    "ended_at": None,
                    "duration_s": 0.0,
                })

            successes_this_turn = 0
            failures_this_turn = 0
            bar = tqdm(plan, desc="Executing plan", unit="cmd", leave=False)
            for step in bar:
                bar.set_postfix({"ok": successes_this_turn, "fail": failures_this_turn}, refresh=True)
                if step["status"] != "pending":
                    continue
                step["started_at"] = time.time()
                print(f"martin: Run {step['index']}/{len(plan)}: {step['cmd']}")
                if step.get("internal_key"):
                    started = time.time()
                    try:
                        # Use researcher's dispatch_internal_ability
                        ok, output = dispatch_internal_ability(step["internal_key"], step.get("payload") or "")
                    except Exception as e:
                        ok = False
                        output = f"(internal error) {e}"
                    step["rc"] = 0 if ok else 1
                    step["stdout"] = output or ""
                    step["ended_at"] = time.time()
                    step["duration_s"] = round(step["ended_at"] - started, 3)
                else:
                    outside = _outside_workspace_path(step["cmd"])
                    if outside and not _confirm_outside_workspace(outside, step["cmd"]):
                        ok, output = False, f"outside workspace blocked ({outside})"
                        stdout_text, stderr_text, rc = "", output, 2
                    elif step.get("risk") == "blocked":
                        ok, output = False, "blocked by command policy"
                        stdout_text, stderr_text, rc = "", output, 2
                    elif step.get("risk") == "high":
                        ok, output = False, "high-risk command requires explicit confirmation"
                        stdout_text, stderr_text, rc = "", output, 2
                    else:
                        allowed, reason = enforce_sandbox(step["cmd"], sandbox_mode, str(Path.cwd()))
                        if not allowed:
                            if _maybe_override_sandbox(reason):
                                ok, stdout_text, stderr_text, rc = _run_cmd_with_worklog(step["cmd"])
                            else:
                                ok, stdout_text, stderr_text, rc = False, "", reason, 2
                        else:
                            ok, stdout_text, stderr_text, rc = _run_cmd_with_worklog(step["cmd"])
                    if rc == 130:
                        print("\033[93mmartin: Command cancelled.\033[0m")
                    output = stdout_text
                    if stderr_text:
                        output = (stdout_text + "\n[stderr]\n" + stderr_text).strip()
                    step["rc"] = rc
                    step["stdout"] = stdout_text or ""
                    step["stderr"] = stderr_text or ""
                    if rc and rc != 0:
                        _record_failed_command(step["cmd"], rc, stderr_text or output or "failed")
                step["ended_at"] = step["ended_at"] or time.time()
                step["duration_s"] = step["duration_s"] or round(step["ended_at"] - step["started_at"], 3)
                step["output"] = output or ""
                if ok:
                    step["status"] = "ok"
                    successes_this_turn += 1
                    _maybe_advance_plan_task(ok)
                    output_path = ""
                    if output:
                        stored = _store_long_output(output, "cmd") if not _privacy_enabled() else ""
                        display = _format_output_for_display(output)
                        print(display)
                        _print_output_summary(output)
                        if stored:
                            output_path = stored
                            print(f"[full output saved to {stored}]")
                    if not _privacy_enabled():
                        try:
                            append_tool_entry({
                                "command": step["cmd"],
                                "cwd": str(Path.cwd()),
                                "rc": step.get("rc"),
                                "ok": ok,
                                "duration_s": step.get("duration_s"),
                                "stdout": step.get("stdout"),
                                "stderr": step.get("stderr"),
                                "output_path": output_path,
                                "risk": step.get("risk"),
                                "risk_reasons": step.get("risk_reasons"),
                                "sandbox_mode": sandbox_mode,
                                "approval_policy": approval_policy,
                            })
                        except Exception:
                            pass
                    try:
                        st = load_state()
                        st["last_command_summary"] = {
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "cmd": step.get("cmd"),
                            "rc": step.get("rc"),
                            "ok": True,
                            "summary": chat_ui.shorten_output(output),
                        }
                        save_state(st)
                    except Exception:
                        pass
                    # Capture last path from successful listing commands.
                    cmd_txt = step["cmd"]
                    if "Get-ChildItem -Path" in cmd_txt:
                        parts = cmd_txt.split("Get-ChildItem -Path", 1)[1].strip()
                        cleaned = parts.replace(" -Force", "").replace("-Force", "").strip()
                        _LAST_PATH = cleaned.strip('"').strip("'")
                        names = []
                        for line in (output or "").splitlines():
                            line = line.rstrip()
                            if not line:
                                continue
                            if line.startswith("Directory:"):
                                continue
                            if line.startswith("Mode") or line.startswith("----"):
                                continue
                            if line[0] in ("d", "-"):
                                cols = re.split(r"\s{2,}", line)
                                if cols:
                                    names.append(cols[-1])
                        if names:
                            _LAST_LISTING[:] = names
                        global _MEMORY_DIRTY
                        _MEMORY_DIRTY = True
                    elif cmd_txt.startswith("command:"):
                        cmd_txt = cmd_txt[len("command:"):].strip()
                    if cmd_txt.startswith("ls -la "):
                        _LAST_PATH = cmd_txt[len("ls -la "):].strip().strip('"').strip("'")
                        names = []
                        for line in (output or "").splitlines():
                            if not line or line.startswith("total "):
                                continue
                            if line[0] in ("d", "-"):
                                parts = line.split()
                                if parts:
                                    names.append(parts[-1])
                        if names:
                            _LAST_LISTING[:] = names
                        _MEMORY_DIRTY = True
                    logger.info("cmd_ok cmd=%s", step["cmd"])
                else:
                    step["status"] = "fail"
                    failures_this_turn += 1
                    output_path = ""
                    if output:
                        stored = _store_long_output(output, "cmd_fail") if not _privacy_enabled() else ""
                        display = _format_output_for_display(output)
                        print(display)
                        _print_output_summary(output)
                        if stored:
                            output_path = stored
                            print(f"[full output saved to {stored}]")
                    try:
                        append_tool_entry({
                            "command": step["cmd"],
                            "cwd": str(Path.cwd()),
                            "rc": step.get("rc"),
                            "ok": ok,
                            "duration_s": step.get("duration_s"),
                            "stdout": step.get("stdout"),
                            "stderr": step.get("stderr"),
                            "output_path": output_path,
                            "risk": step.get("risk"),
                            "risk_reasons": step.get("risk_reasons"),
                            "sandbox_mode": sandbox_mode,
                            "approval_policy": approval_policy,
                        })
                    except Exception:
                        pass
                    try:
                        st = load_state()
                        st["last_command_summary"] = {
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "cmd": step.get("cmd"),
                            "rc": step.get("rc"),
                            "ok": False,
                            "summary": chat_ui.shorten_output(output),
                        }
                        save_state(st)
                    except Exception:
                        pass
                    logger.info("cmd_fail cmd=%s", step["cmd"])
                    # Use researcher's diagnose_failure
                    diagnosis = diagnose_failure(step["cmd"], output or "")
                    print(f"\033[93mmartin (diagnosis): {diagnosis}\033[0m")
                    try:
                        if agent_mode:
                            rerun_option = "yes"
                        else:
                            rerun_option = input("\033[92mmartin: Apply suggested fix commands now, or abort? (yes/no/abort)\033[0m ").strip().lower()
                    except (EOFError, KeyboardInterrupt):
                        rerun_option = "no"
                    if rerun_option == 'yes':
                        interaction_history.append("martin (diagnosis): " + diagnosis)
                        new_terminal_commands = extract_commands(diagnosis) # Use researcher's extract_commands
                        if not new_terminal_commands:
                            print("\033[93mmartin: Diagnosis included no runnable commands.\033[0m")
                        else:
                            print("\n\033[96mmartin: Proposed FIX commands (review):\033[0m")
                            for i2, c2 in enumerate(new_terminal_commands, 1):
                                print(f"  {i2}. {c2}")
                            def _edit_fix_commands(cmds: List[str]) -> List[str]:
                                edited = []
                                for idx, c in enumerate(cmds, 1):
                                    try:
                                        repl = input(f"\033[93mEdit FIX command {idx} (enter to keep):\033[0m ").strip()
                                    except (EOFError, KeyboardInterrupt):
                                        repl = ""
                                    edited.append(repl if repl else c)
                                return edited
                            try:
                                if agent_mode or approval_policy in ("never", "on-failure"):
                                    confirm_fix = "yes"
                                else:
                                    while True:
                                        confirm_fix = input("\033[93mApprove running FIX commands? (yes/no/abort/edit/inline/editor/explain/dry-run)\033[0m ").strip().lower()
                                        if confirm_fix == "explain":
                                            print("\033[96mmartin: Rationale:\033[0m Fix commands proposed from diagnosis.")
                                            try:
                                                log_event(load_state(), "fix_command_decision", action="explain", count=len(new_terminal_commands))
                                            except Exception:
                                                pass
                                            continue
                                        if confirm_fix == "edit":
                                            new_terminal_commands = _edit_fix_commands(new_terminal_commands)
                                            try:
                                                log_event(load_state(), "fix_command_decision", action="edit", count=len(new_terminal_commands))
                                            except Exception:
                                                pass
                                            continue
                                        if confirm_fix == "inline":
                                            new_terminal_commands = edit_commands_inline(new_terminal_commands)
                                            try:
                                                log_event(load_state(), "fix_command_decision", action="inline", count=len(new_terminal_commands))
                                            except Exception:
                                                pass
                                            continue
                                        if confirm_fix == "editor":
                                            new_terminal_commands = edit_commands_in_editor(new_terminal_commands)
                                            try:
                                                log_event(load_state(), "fix_command_decision", action="editor", count=len(new_terminal_commands))
                                            except Exception:
                                                pass
                                            continue
                                        if confirm_fix == "dry-run":
                                            print("\033[92mmartin: Dry run only; fix commands not executed.\033[0m")
                                            try:
                                                log_event(load_state(), "fix_command_decision", action="dry-run", count=len(new_terminal_commands))
                                            except Exception:
                                                pass
                                            new_terminal_commands = []
                                            break
                                        break
                            except (EOFError, KeyboardInterrupt):
                                confirm_fix = "no"
                            if confirm_fix == "abort":
                                print("\033[92mmartin: Aborting per request.\033[0m")
                                try:
                                    log_event(load_state(), "fix_command_decision", action="abort", count=len(new_terminal_commands))
                                except Exception:
                                    pass
                                break
                            elif confirm_fix == "yes":
                                try:
                                    log_event(load_state(), "fix_command_decision", action="yes", count=len(new_terminal_commands))
                                except Exception:
                                    pass
                                for new_command in new_terminal_commands:
                                    print(f"Executing (fix): {new_command}")
                                    risk_fix = classify_command_risk(new_command, command_allowlist, command_denylist)
                                    if risk_fix["level"] == "blocked":
                                        ok2, out2, err2, rc2 = False, "", f"command blocked: {risk_fix['level']} ({', '.join(risk_fix['reasons'])})", 2
                                    elif risk_fix["level"] == "high":
                                        try:
                                            confirm_high = input("\033[91mHigh-risk FIX command detected. Type YES to proceed:\033[0m ").strip().lower()
                                        except (EOFError, KeyboardInterrupt):
                                            confirm_high = "no"
                                        if confirm_high != "yes":
                                            ok2, out2, err2, rc2 = False, "", f"command blocked: {risk_fix['level']} ({', '.join(risk_fix['reasons'])})", 2
                                        else:
                                            allowed, reason = enforce_sandbox(new_command, sandbox_mode, str(Path.cwd()))
                                            if not allowed:
                                                if _maybe_override_sandbox(reason):
                                                    ok2, out2, err2, rc2 = _run_cmd_with_worklog(new_command)
                                                else:
                                                    ok2, out2, err2, rc2 = False, "", f"sandbox blocked: {reason}", 2
                                            else:
                                                ok2, out2, err2, rc2 = _run_cmd_with_worklog(new_command)
                                    else:
                                        allowed, reason = enforce_sandbox(new_command, sandbox_mode, str(Path.cwd()))
                                        if not allowed:
                                            if _maybe_override_sandbox(reason):
                                                ok2, out2, err2, rc2 = _run_cmd_with_worklog(new_command)
                                            else:
                                                ok2, out2, err2, rc2 = False, "", f"sandbox blocked: {reason}", 2
                                        else:
                                            ok2, out2, err2, rc2 = _run_cmd_with_worklog(new_command)
                                    combined = out2
                                    if err2:
                                        combined = (out2 + "\n[stderr]\n" + err2).strip()
                                    try:
                                        append_tool_entry({
                                            "command": new_command,
                                            "cwd": str(Path.cwd()),
                                            "rc": rc2,
                                            "ok": ok2,
                                            "duration_s": None,
                                            "stdout": out2,
                                            "stderr": err2,
                                            "output_path": "",
                                            "risk": risk_fix["level"],
                                            "risk_reasons": risk_fix["reasons"],
                                            "sandbox_mode": sandbox_mode,
                                            "approval_policy": approval_policy,
                                        })
                                    except Exception:
                                        pass
                                    out2 = combined
                                    if ok2:
                                        successes_this_turn += 1
                                    else:
                                        failures_this_turn += 1
                            else:
                                print("\033[92mmartin: Fix not applied. Continuing.\033[0m")
                                try:
                                    log_event(load_state(), "fix_command_decision", action="no", count=len(new_terminal_commands))
                                except Exception:
                                    pass
                    elif rerun_option == 'abort':
                        print("\033[92mmartin: Aborting the operation.\033[0m")
                        for rest in plan:
                            if rest["status"] == "pending":
                                rest["status"] = "skipped"
                        break
                    else:
                        print("\033[92mmartin: Acknowledged - not applying fix.\033[0m")
                sess.record_cmd(0 if ok else 1) # Record command outcome
            bar.close()
            print(f"\033[92mmartin: Done. OK: {successes_this_turn}, FAIL: {failures_this_turn}\033[0m")
            logger.info("chat_turn_complete ok=%d fail=%d", successes_this_turn, failures_this_turn)
            try:
                st = load_state()
                st["last_plan"] = {"steps": terminal_commands, "status": "complete", "ok": successes_this_turn, "fail": failures_this_turn}
                if behavior_flags.get("summaries"):
                    st["last_action_summary"] = f"ran {len(terminal_commands)} command(s): OK {successes_this_turn}, FAIL {failures_this_turn}"
                if behavior_flags.get("context_block"):
                    st["active_context"] = _build_active_context(st)
                save_state(st)
            except Exception:
                pass
            if behavior_flags.get("summaries"):
                try:
                    st = load_state()
                    summary = st.get("last_action_summary", "")
                    next_action = ""
                    tasks = st.get("tasks", []) if isinstance(st.get("tasks"), list) else []
                    if tasks:
                        next_action = tasks[0].get("text", "")
                    if summary:
                        line = f"martin: Summary: {summary}"
                        if next_action:
                            line += f" | Next: {next_action}"
                        print(line)
                except Exception:
                    pass
        _mo_exit_check()
        _prompt_clock("Clock-out")

    finally:
        if original_input is not None:
            builtins.input = original_input
        if test_bridge:
            try:
                test_bridge.stop()
            except Exception:
                pass
        server.stop()

    if args.transcript:
        try:
            from researcher.file_utils import preview_write
            if _privacy_enabled():
                print("martin: Privacy mode is on; transcript write skipped.")
                raise Exception("privacy enabled")
            content = "\n".join(transcript) + "\n"
            out_path = Path(args.transcript)
            if preview_write(out_path, content):
                out_path.write_text(content, encoding="utf-8")
        except Exception as e:
            print(f"Warning: failed to write transcript ({e})", file=sys.stderr)
    sess.end()
    # Persist memory snapshot (archive between runs)
    if _MEMORY_DIRTY:
        try:
            st = load_state()
            snapshot = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "last_path": _LAST_PATH,
                "last_listing": _LAST_LISTING[:100],
            }
            history = st.get("memory_history", [])
            if not isinstance(history, list):
                history = []
            history.append(snapshot)
            st["memory_history"] = history[-20:]
            st["memory"] = snapshot
            # Archive transcript for this run
            if not _privacy_enabled():
                t_hist = st.get("session_history", [])
                if not isinstance(t_hist, list):
                    t_hist = []
                if session_transcript:
                    t_hist.append({
                        "ts": snapshot["ts"],
                        "transcript": session_transcript[-500:],
                    })
                st["session_history"] = t_hist[-10:]
                st.pop("session_memory", None)
            save_state(st)
        except Exception:
            pass
    try:
        if not _privacy_enabled():
            st = load_state()
            st["resume_snapshot"] = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "last_path": _LAST_PATH,
                "last_listing": _LAST_LISTING[:100],
                "last_plan": st.get("last_plan", {}),
                "context_cache": st.get("context_cache", {}),
                "transcript_tail": session_transcript[-200:],
                "cwd": str(Path.cwd()),
            }
            save_state(st)
    except Exception:
        pass
    try:
        if readline_mod and history_path:
            readline_mod.write_history_file(str(history_path))
    except Exception:
        pass
    return 0

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="researcher CLI (skeleton)")
    parser.add_argument("--version", action="store_true", help="Print version and exit")
    sub = parser.add_subparsers(dest="command", required=False)

    p_status = sub.add_parser("status", help="Show config summary")
    p_status.add_argument("--simple-index", action="store_true", help="Force SimpleIndex (skip FAISS)")
    p_status.add_argument("--json", action="store_true", help="Emit JSON output")
    p_status.set_defaults(func=lambda cfg, args: cmd_status(cfg, force_simple=args.simple_index, as_json=args.json))

    p_ingest = sub.add_parser("ingest", help="Ingest files into index")
    p_ingest.add_argument("files", nargs="+", help="Files to ingest")
    p_ingest.add_argument("--simple-index", action="store_true", help="Force SimpleIndex (skip FAISS)")
    p_ingest.add_argument("--ext", default="", help="Comma-separated list of file extensions to ingest")
    p_ingest.add_argument("--max-files", type=int, default=0, help="Max files to ingest (0 = no limit)")
    p_ingest.add_argument("--json", action="store_true", help="Emit JSON output")
    p_ingest.set_defaults(func=lambda cfg, args: cmd_ingest(cfg, args.files, force_simple=args.simple_index, exts=[e for e in args.ext.split(",") if e], max_files=args.max_files, as_json=args.json))

    p_ask = sub.add_parser("ask", help="Ask the local index")
    p_ask.add_argument("prompt", nargs="*", help="Prompt text (or use --stdin)")
    p_ask.add_argument("--stdin", action="store_true", help="Read prompt from stdin")
    p_ask.add_argument("-k", type=int, default=5, help="Top-k results")
    p_ask.add_argument("--use-llm", action="store_true", help="Force local LLM generation (ollama)")
    p_ask.add_argument("--cloud-mode", choices=["off", "auto", "always"], default="off", help="Call cloud CLI after local retrieval")
    p_ask.add_argument("--cloud-cmd", default=os.environ.get("CLOUD_CMD", ""), help="Cloud command template with {prompt} placeholder")
    p_ask.add_argument("--cloud-threshold", type=float, default=None, help="Top score threshold for auto cloud hop (default from config)")
    p_ask.add_argument("--simple-index", action="store_true", help="Force SimpleIndex (skip FAISS)")
    p_ask.add_argument("--json", action="store_true", help="Emit JSON output")
    p_ask.set_defaults(func=lambda cfg, args: cmd_ask(cfg, read_prompt(args), args.k, use_llm=args.use_llm, cloud_mode=args.cloud_mode, cloud_cmd=args.cloud_cmd, cloud_threshold=args.cloud_threshold, force_simple=args.simple_index, as_json=args.json))

    # New chat subcommand for the interactive main loop
    p_chat = sub.add_parser("chat", help="Start an interactive chat session with the researcher agent")
    p_chat.add_argument("--transcript", default="", help="Write a transcript to this path")
    p_chat.set_defaults(func=lambda cfg, args: cmd_chat(cfg, args))

    p_srv = sub.add_parser("serve", help="Start a local HTTP service")
    p_srv.add_argument("--host", default="127.0.0.1", help="Host to bind")
    p_srv.add_argument("--port", type=int, default=8088, help="Port to bind")
    p_srv.set_defaults(func=handle_serve)

    add_plan_command(sub)
    add_supervise_command(sub)
    add_abilities_command(sub)
    add_resources_command(sub)
    add_tui_command(sub)

    return parser


def main(argv: List[str] = None) -> int:
    cfg = load_config()
    try:
        from researcher import llm_utils
        ui_cfg = cfg.get("ui", {}) or {}
        llm_utils.SHOW_API_BARS = bool(ui_cfg.get("api_progress", False))
    except Exception:
        pass
    parser = build_parser()
    if argv is None:
        argv = sys.argv[1:]
    if "--version" in argv:
        print(__version__)
        return 0
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        return cmd_chat(cfg, argparse.Namespace(transcript=""))
    try:
        _get_cli_logger(cfg).info("cli_command %s", args.command)
        return args.func(cfg, args)
    except Exception as e:
        try:
            _get_cli_logger(cfg).exception("cli_exception %s", args.command)
            st = load_state()
            log_event(st, "cli_exception", command=args.command, error=str(e))
        except Exception:
            pass
        print(f"error: {e}", file=sys.stderr)
        return 1


def add_plan_command(sub):
    p_plan = sub.add_parser("plan", help="Extract and (optionally) run command plan from text")
    p_plan.add_argument("prompt", nargs="*", help="Text containing command: lines (or use --stdin)")
    p_plan.add_argument("--stdin", action="store_true", help="Read prompt from stdin")
    p_plan.add_argument("--run", action="store_true", help="Run extracted commands (non-interactive)")
    p_plan.add_argument("--dry-run", action="store_true", help="Print commands without executing")
    p_plan.add_argument("--timeout", type=int, default=120, help="Per-command timeout seconds")
    p_plan.set_defaults(func=handle_plan)


def handle_plan(cfg, args) -> int:
    from researcher.command_utils import extract_commands, classify_command_risk, edit_commands_in_editor, edit_commands_inline
    from researcher.orchestrator import dispatch_internal_ability
    from researcher.runner import run_command_smart_capture, enforce_sandbox
    from researcher.tool_ledger import append_tool_entry
    # logger = setup_logger(Path(cfg.get("data_paths", {}).get("logs", "logs")) / "local.log") # No longer needed directly here
    st = load_state() # Load state for logging
    prompt = read_prompt(args)
    # Replaced sanitize_and_extract with extract_commands from researcher.command_utils
    # The 'sanitized' concept might still be relevant for prompting, but not for command extraction itself.
    # For now, we only extract commands directly from the prompt.
    cmds = extract_commands(prompt) 
    
    # Original Martin had a sanitization step here for the prompt.
    # We will assume prompt is already sanitized or sanitization happens earlier if needed.
    # For now, just print the prompt directly.
    print("Prompt:", prompt) 

    if not cmds:
        print("No commands extracted.")
        return 0
    print("Command plan:")
    for i, c in enumerate(cmds, 1):
        print(f"  {i}. {c}")
    log_event(st, "plan_command_extracted", cmds_count=len(cmds)) # Use state_manager's log_event
    
    if args.dry_run:
        print("Dry run: commands extracted, no execution.")
        return 0
    if args.run:
        # Replaced run_plan with execution loop using run_command_smart and dispatch_internal_ability
        results = []
        any_fail = False
        exec_cfg = cfg.get("execution", {}) or {}
        approval_policy = (exec_cfg.get("approval_policy") or "on-request").lower()
        sandbox_mode = (exec_cfg.get("sandbox_mode") or "workspace-write").lower()
        command_allowlist = exec_cfg.get("command_allowlist") or []
        command_denylist = exec_cfg.get("command_denylist") or []
        def _maybe_override_sandbox(block_reason: str) -> bool:
            if approval_policy == "never":
                return False
            try:
                resp = input(f"\033[93mmartin: Sandbox blocked this command ({block_reason}). Override once? (yes/no)\033[0m ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                resp = "no"
            return resp == "yes"
        if approval_policy == "on-request":
            def _edit_commands(cmds: List[str]) -> List[str]:
                edited = []
                for idx, c in enumerate(cmds, 1):
                    try:
                        repl = input(f"\033[93mEdit command {idx} (enter to keep):\033[0m ").strip()
                    except (EOFError, KeyboardInterrupt):
                        repl = ""
                    edited.append(repl if repl else c)
                return edited
            while True:
                try:
                    confirm_run = input("\033[93mApprove running this command plan? (yes/no/abort/edit/inline/editor/explain/dry-run)\033[0m ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    confirm_run = "no"
                if confirm_run == "explain":
                    print("\033[96mmartin: Rationale:\033[0m plan extracted from provided text.")
                    continue
                if confirm_run == "edit":
                    cmds = _edit_commands(cmds)
                    continue
                if confirm_run == "inline":
                    cmds = edit_commands_inline(cmds)
                    continue
                if confirm_run == "editor":
                    cmds = edit_commands_in_editor(cmds)
                    continue
                if confirm_run == "dry-run":
                    print("\033[92mmartin: Dry run only; no commands executed.\033[0m")
                    return 0
                break
            if confirm_run != "yes":
                print("\033[92mmartin: Aborting per approval policy.\033[0m")
                return 1
        try:
            st = load_state()
            st["last_plan"] = {"steps": cmds, "status": "pending"}
            save_state(st)
        except Exception:
            pass
        for cmd_str in cmds:
            raw_cmd = cmd_str.replace("command:", "", 1).strip() if cmd_str.lower().startswith("command:") else cmd_str
            
            # Check if it's an internal ability call (using the same parsing as in cmd_chat)
            def _parse_internal_cmd_for_plan(c: str) -> Tuple[Optional[str], Optional[str]]:
                s = c.strip()
                if not s.lower().startswith("martin."): # Still using "martin." protocol
                    return (None, None)
                body = s[len("martin."):].strip()
                if " " in body:
                    key, payload = body.split(" ", 1)
                elif ":" in body:
                    key, payload = body.split(":", 1)
                else:
                    key, payload = body, ""
                return (key.strip(), payload.strip())

            ability_key, payload_txt = _parse_internal_cmd_for_plan(raw_cmd)
            risk = {"level": "", "reasons": []}
            
            ok, output = False, ""
            stdout_text = ""
            stderr_text = ""
            rc = 1
            if ability_key:
                try:
                    ok, output = dispatch_internal_ability(ability_key, payload_txt or "")
                except Exception as e:
                    output = f"(internal error) {e}"
                rc = 0 if ok else 1
                stdout_text = output or ""
            else:
                risk = classify_command_risk(cmd_str, command_allowlist, command_denylist)
                if risk["level"] == "blocked":
                    ok = False
                    rc = 2
                    stdout_text = ""
                    stderr_text = f"command blocked: {risk['level']} ({', '.join(risk['reasons'])})"
                elif risk["level"] == "high":
                    try:
                        confirm_high = input("\033[91mHigh-risk command detected. Type YES to proceed:\033[0m ").strip().lower()
                    except (EOFError, KeyboardInterrupt):
                        confirm_high = "no"
                    if confirm_high != "yes":
                        ok = False
                        rc = 2
                        stdout_text = ""
                        stderr_text = f"command blocked: {risk['level']} ({', '.join(risk['reasons'])})"
                    else:
                        allowed, reason = enforce_sandbox(cmd_str, sandbox_mode, str(Path.cwd()))
                        if not allowed:
                            if _maybe_override_sandbox(reason):
                                ok, stdout_text, stderr_text, rc = run_command_smart_capture(cmd_str)
                            else:
                                ok = False
                                rc = 2
                                stdout_text = ""
                                stderr_text = f"sandbox blocked: {reason}"
                        else:
                            ok, stdout_text, stderr_text, rc = run_command_smart_capture(cmd_str)
                else:
                    allowed, reason = enforce_sandbox(cmd_str, sandbox_mode, str(Path.cwd()))
                    if not allowed:
                        if _maybe_override_sandbox(reason):
                            ok, stdout_text, stderr_text, rc = run_command_smart_capture(cmd_str)
                        else:
                            ok = False
                            rc = 2
                            stdout_text = ""
                            stderr_text = f"sandbox blocked: {reason}"
                    else:
                        ok, stdout_text, stderr_text, rc = run_command_smart_capture(cmd_str)
            try:
                append_tool_entry({
                    "command": cmd_str,
                    "cwd": str(Path.cwd()),
                    "rc": rc,
                    "ok": ok,
                    "duration_s": None,
                    "stdout": stdout_text,
                    "stderr": stderr_text,
                    "output_path": "",
                    "risk": risk["level"] if not ability_key else "",
                    "risk_reasons": risk["reasons"] if not ability_key else [],
                    "sandbox_mode": sandbox_mode,
                    "approval_policy": approval_policy,
                })
            except Exception:
                pass
            
            results.append((cmd_str, rc, stdout_text + (("\n" + stderr_text) if stderr_text else "")))
            any_fail = any_fail or (rc != 0)
            
            status = "OK" if rc == 0 else f"FAIL({rc})"
            combined = stdout_text
            if stderr_text:
                combined = f"{combined}\n[stderr]\n{stderr_text}"
            print(f"[{status}] {cmd_str}\n{combined}\n")
            log_event(
                st,
                "plan_command_result",
                cmd=cmd_str,
                rc=rc,
                stdout=(stdout_text or "")[-4000:],
                stderr=(stderr_text or "")[-4000:],
            )
            
        log_event(st, "plan_command_run", cmds_count=len(cmds)) # Use state_manager's log_event
        try:
            st = load_state()
            st["last_plan"] = {"steps": cmds, "status": "complete", "ok": 0 if any_fail else 1}
            save_state(st)
        except Exception:
            pass
        return 1 if any_fail else 0
    return 0


def add_supervise_command(sub):
    p_sup = sub.add_parser("nudge", help="Check logs and print nudge if idle")
    p_sup.add_argument("--idle-seconds", type=int, default=300, help="Idle threshold")
    p_sup.set_defaults(func=handle_nudge)

    p_loop = sub.add_parser("supervise", help="Run a periodic supervisor loop")
    p_loop.add_argument("--idle-seconds", type=int, default=300, help="Idle threshold")
    p_loop.add_argument("--sleep-seconds", type=int, default=30, help="Loop sleep interval")
    p_loop.add_argument("--max-prompts", type=int, default=3, help="Max prompts before exit (0 = unlimited)")
    p_loop.add_argument("--prompt", default="Agent appears idle; please continue or report status.", help="Prompt text")
    p_loop.set_defaults(func=handle_supervise)

    p_lib = sub.add_parser("librarian", help="Control the Librarian process")
    p_lib.add_argument("action", choices=["status", "start", "shutdown"], help="Action to perform")
    p_lib.add_argument("--debug", action="store_true", help="Start Librarian in debug mode")
    p_lib.add_argument("--verbose", action="store_true", help="Verbose status diagnostics")
    p_lib.set_defaults(func=handle_librarian)


def handle_nudge(cfg, args) -> int:
    # logger = setup_logger(Path(cfg.get("data_paths", {}).get("logs", "logs")) / "local.log") # No longer needed directly here
    # nudge_message will eventually be updated to use state_manager's ledger for recent activity
    logs_path = Path(cfg.get("data_paths", {}).get("logs", "logs")) / "local.log" # Still uses old log for now
    msg = nudge_message(logs_path, idle_seconds=args.idle_seconds)
    print(msg)
    return 0


def handle_supervise(cfg, args) -> int:
    from researcher.supervisor import run_supervisor
    logs_path = Path(cfg.get("data_paths", {}).get("logs", "logs")) / "local.log"
    run_supervisor(
        logs_path=logs_path,
        idle_seconds=args.idle_seconds,
        sleep_seconds=args.sleep_seconds,
        prompt=args.prompt,
        max_prompts=args.max_prompts,
    )
    return 0


def handle_serve(cfg, args) -> int:
    from researcher.service import run_server
    run_server(host=args.host, port=args.port)
    return 0


def handle_librarian(cfg, args) -> int:
    from researcher.librarian_client import LibrarianClient
    import subprocess
    client = LibrarianClient()
    if args.action == "status":
        resp = client.get_status()
        if getattr(args, "verbose", False):
            import time as _time
            t0 = _time.time()
            ping = client.get_status()
            latency_ms = int((_time.time() - t0) * 1000)
            resp = {
                "status": resp.get("status"),
                "message": resp.get("message"),
                "latency_ms": latency_ms,
                "cloud_configured": bool(os.environ.get("RESEARCHER_CLOUD_API_KEY") or os.environ.get("OPENAI_API_KEY")),
                "heartbeat_age_s": resp.get("heartbeat_age_s"),
                "last_request_ts": resp.get("last_request_ts"),
                "cloud_backoff_until": resp.get("cloud_backoff_until"),
                "cloud_breaker_until": resp.get("cloud_breaker_until"),
            }
            try:
                last_error = None
                if LEDGER_FILE.exists():
                    with open(LEDGER_FILE, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                    for line in reversed(lines):
                        try:
                            record = json.loads(line)
                            entry = record.get("entry", {})
                            if entry.get("event") == "librarian_error":
                                last_error = entry.get("data", {})
                                break
                        except Exception:
                            continue
                resp["last_error"] = last_error
            except Exception:
                resp["last_error"] = None
        print(resp)
        client.close()
        return 0 if resp.get("status") == "success" else 1
    if args.action == "shutdown":
        resp = client.shutdown()
        print(resp)
        return 0 if resp.get("status") == "success" else 1
    if args.action == "start":
        resp = client.get_status()
        if resp.get("status") == "success":
            print("Librarian already running.")
            client.close()
            return 0
        
        # Start the process
        cmd = [sys.executable, "-m", "researcher.librarian"]
        if args.debug:
            cmd = [sys.executable, "-m", "researcher.librarian"]
        
        print("Librarian start requested...")
        
        # Use DETACHED_PROCESS on Windows to prevent freezing
        creation_flags = 0
        if os.name == 'nt':
            creation_flags = subprocess.DETACHED_PROCESS
            
        subprocess.Popen(cmd, creationflags=creation_flags)
        
        # Give it a moment to start up
        time.sleep(2)
        
        # Verify it's running
        status_resp = client.get_status()
        if status_resp.get("status") == "success":
            print("Librarian started successfully.")
            client.close()
            return 0
        else:
            print("Error: Librarian failed to start or is not responding.")
            # Try to fetch and display the last error from the state log
            from researcher.state_manager import LEDGER_FILE
            try:
                if not LEDGER_FILE.exists():
                    print("Ledger file not found.")
                else:
                    with open(LEDGER_FILE, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                    
                    last_error = None
                    for line in reversed(lines):
                        try:
                            record = json.loads(line)
                            entry = record.get("entry", {})
                            if entry.get("event") == "librarian_error":
                                last_error = entry.get("data", {})
                                break
                        except json.JSONDecodeError:
                            continue # Skip corrupted lines
                            
                    if last_error:
                        print("\n--- Last Recorded Librarian Error ---")
                        print(json.dumps(last_error, indent=2))
                        print("------------------------------------")
                    else:
                        print("No specific librarian error was found in the ledger.")
            except Exception as e:
                print(f"Could not read ledger file for errors: {e}")
            client.close()
            return 1
            
    return 1


def add_abilities_command(sub):
    p_abilities = sub.add_parser("abilities", help="List or run internal abilities")
    p_abilities.add_argument("name", nargs="?", help="Ability name to run (optional)")
    p_abilities.add_argument("--payload", default="", help="Payload text for the ability")
    p_abilities.set_defaults(func=handle_abilities)


def handle_abilities(cfg, args) -> int:
    from researcher.orchestrator import ABILITY_REGISTRY, dispatch_internal_ability
    if not args.name:
        print("Abilities:")
        for key in sorted(ABILITY_REGISTRY.keys()):
            print(f"- {key}")
    return 0


def add_resources_command(sub):
    p_resources = sub.add_parser("resources", help="List readable resources under the repo root")
    p_resources.add_argument("--max-items", type=int, default=200, help="Max items to list")
    p_resources.add_argument("--max-depth", type=int, default=4, help="Max directory depth to scan")
    p_resources.set_defaults(func=handle_resources)

    p_resource = sub.add_parser("resource", help="Read a resource path under the repo root")
    p_resource.add_argument("path", help="Resource path (relative to repo root)")
    p_resource.add_argument("--max-bytes", type=int, default=65536, help="Max bytes to read")
    p_resource.set_defaults(func=handle_resource)


def handle_resources(cfg, args) -> int:
    from researcher.resource_registry import list_resources
    items = list_resources(max_items=args.max_items, max_depth=args.max_depth)
    print(json.dumps({"root": str(ROOT_DIR), "items": items}, ensure_ascii=False, indent=2))
    return 0


def handle_resource(cfg, args) -> int:
    from researcher.resource_registry import read_resource
    ok, result = read_resource(args.path, max_bytes=args.max_bytes)
    result["ok"] = ok
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
    ok, output = dispatch_internal_ability(args.name, args.payload or "")
    if ok:
        print(output)
        return 0
    print(f"error: {output}", file=sys.stderr)
    return 1


def add_tui_command(sub):
    p_tui = sub.add_parser("tui", help="Start the Rich-based TUI shell")
    p_tui.set_defaults(func=lambda cfg, args: handle_tui(cfg, args))


def handle_tui(cfg, args) -> int:
    run_tui()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        crash_path = _write_crash_log(exc)
        if crash_path:
            print(f"martin: Crash details saved to {crash_path}", file=sys.stderr)
        raise
