import argparse
import datetime
import logging
import os
import re
import sys
import time
import json # Added for main loop
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

if __package__ is None and __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from researcher import sanitize
from researcher.config_loader import load_config, ensure_dirs
from researcher.index import SimpleIndex, FaissIndex
from researcher.ingester import ingest_files
from researcher.log_utils import setup_logger
from researcher.provenance import build_response
from researcher.answer import compose_answer
# Removed: from researcher.martin_behaviors import sanitize_and_extract, run_plan
from researcher.supervisor import nudge_message
from researcher.local_llm import run_ollama_chat
from researcher import chat_ui
from researcher.tui_shell import run_tui

# New imports for Librarian client
from researcher.librarian_client import LibrarianClient
from researcher.socket_server import SocketServer

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


def _privacy_enabled_state() -> bool:
    try:
        st = load_state()
        return st.get("session_privacy") == "no-log"
    except Exception:
        return False


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
    return {
        "version": __version__,
        "model_main": MODEL_MAIN,
        "local_model": str(cfg.get("local_model")),
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
        },
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
        ("embedding_model", payload.get("embedding_model")),
        ("index_type", payload.get("index_type")),
        ("index_path", payload.get("index_path")),
        ("index_docs", str(payload.get("index_docs"))),
        ("index_load_ms", f"{payload.get('index_load_ms', 0):.2f}"),
    ]
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
    ctx = get_system_context()
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
    ensure_dirs(cfg)
    st = load_state()
    _get_cli_logger(cfg).info("ingest paths=%d force_simple=%s max_files=%d", len(paths), force_simple, max_files)
    expanded = _collect_ingest_files(paths, exts=exts, max_files=max_files)
    existing_paths = [p for p in expanded if Path(p).exists()]
    if not existing_paths:
        msg = "No valid files found to ingest."
        log_event(st, "ingest_command_failed", files_count=0, error="no_valid_files")
        if as_json:
            print(json.dumps({"ok": False, "error": "no_valid_files"}, ensure_ascii=False))
        else:
            print(msg, file=sys.stderr)
        return 1

    idx = _load_index(cfg, force_simple=force_simple)
    files = [Path(p) for p in existing_paths]
    local_result = ingest_files(idx, files)
    if hasattr(idx, "save"):
        idx.save()
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
    if cfg.get("local_llm_enabled") or use_llm:
        ctx = "\n".join([meta.get("chunk", "") for _, meta in hits][:3])
        llm_prompt = f"Context:\n{ctx}\n\nUser question:\n{prompt}\n\nAnswer concisely. If no context, say so."
        llm_answer = run_ollama_chat(cfg.get("local_model", "phi3"), llm_prompt, cfg.get("ollama_host", "http://localhost:11434"))
        log_event(st, "ask_local_llm", llm_used=bool(llm_answer)) # Use state_manager's log_event
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
    from researcher.llm_utils import _post_responses, _extract_output_text, MODEL_MAIN, interaction_history, diagnose_failure, current_username, rephraser
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

    def _handle_librarian_notification(message: Dict[str, Any]) -> None:
        try:
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

    def _auto_context_surface(reason: str) -> None:
        nonlocal context_cache
        try:
            st = load_state()
            prev = st.get("context_cache", {}) if isinstance(st, dict) else {}
            from researcher.context_harvest import gather_context
            context_cache = gather_context(Path.cwd(), max_recent=int(cfg.get("context", {}).get("max_recent", 10)))
            st = load_state()
            st["context_cache"] = context_cache
            save_state(st)
            delta = _context_delta(prev if isinstance(prev, dict) else {}, context_cache)
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
        print("\033[96mmartin: Preflight checks\033[0m")
        for key, val in checks:
            print(f"- {key}: {val}")
        missing = [c for c in checks if c[1] in ("missing", "none (run /tests)")]
        if missing:
            print("martin: Next steps: address missing items before heavy changes.")
        append_worklog("plan", "preflight checks complete")

    def _ensure_handle() -> str:
        st = load_state()
        handle = ""
        if isinstance(st, dict):
            handle = st.get("operator_handle", "") or ""
        if not handle:
            default_handle = current_username or "user"
            try:
                entered = input(f"martin: Handle for logbook? (enter for {default_handle}) ").strip()
            except (EOFError, KeyboardInterrupt):
                entered = ""
            handle = entered or default_handle
            if isinstance(st, dict):
                st["operator_handle"] = handle
                save_state(st)
        return handle

    def _prompt_clock(action: str) -> None:
        handle = _ensure_handle()
        try:
            note = input(f"martin: {action} note (or .skip <reason>): ").strip()
        except (EOFError, KeyboardInterrupt):
            note = ".skip interrupted"
        if note.startswith(".skip"):
            reason = note.replace(".skip", "", 1).strip() or "no reason"
            append_logbook_entry(handle, action, "", skipped_reason=reason)
            append_worklog("thinking", f"{action} skipped: {reason}")
            return
        append_logbook_entry(handle, action, note or "ok")
        append_worklog("doing", f"{action} recorded")

    def _run_cmd_with_worklog(cmd: str) -> Tuple[bool, str, str, int]:
        append_worklog("doing", f"run: {cmd}")
        ok, stdout, stderr, rc = run_command_smart_capture(cmd)
        if rc == 130:
            append_worklog("cancel", f"rc=130 {cmd}")
        else:
            append_worklog("done", f"rc={rc} {cmd}")
        return ok, stdout, stderr, rc

    def _format_review_response(text: str) -> str:
        if not text:
            return "Findings:\n- None.\n\nQuestions:\n- None.\n\nTests:\n- Not run."
        lower = text.lower()
        has_findings = "findings" in lower
        has_questions = "questions" in lower
        has_tests = "tests" in lower or "testing" in lower
        if has_findings and has_questions and has_tests:
            return text
        body = text.strip()
        return (
            "Findings:\n"
            "- " + (body.replace("\n", "\n- ") if body else "None.") + "\n\n"
            "Questions:\n"
            "- None.\n\n"
            "Tests:\n"
            "- Not run."
        )

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
        handler=_handle_librarian_notification
    )
    server.start()
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
        _mo_preflight_check()
        _prompt_clock("Clock-in")
        _auto_context_surface("session start")
        _maybe_prompt_retry()
        try:
            st = load_state()
            if not st.get("onboarding_complete"):
                _run_onboarding()
        except Exception:
            pass
        logger = _get_cli_logger(cfg)
        logger.info("chat_start")
        last_user_request = ""
        agent_mode = False
        cloud_enabled = bool(cfg.get("cloud", {}).get("enabled"))
        if cfg.get("local_only"):
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
                context_cache = gather_context(Path.cwd(), max_recent=int(cfg.get("context", {}).get("max_recent", 10)))
                st = load_state()
                st["context_cache"] = context_cache
                save_state(st)
            chat_ui.print_context_summary(context_cache)
            try:
                last_cmd_summary = load_state().get("last_command_summary", {}) or {}
            except Exception:
                last_cmd_summary = {}
            warn = "local-only" if cfg.get("local_only") else ("cloud-off" if not cloud_enabled else "")
            chat_ui.render_status_banner(
                context_cache,
                last_cmd_summary,
                mode=("agent" if agent_mode else "manual"),
                model_info=MODEL_MAIN,
                warnings=warn,
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
                context_cache = gather_context(Path.cwd(), max_recent=int(cfg.get("context", {}).get("max_recent", 10)))
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
                user_input = input("\033[94mYou:\033[0m ")
            except (EOFError, KeyboardInterrupt):
                print("\n\033[92mmartin: Farewell.\033[0m")
                logger.info("chat_end reason=interrupt")
                break

            if user_input.lower() in ('quit', 'exit'):
                print("\033[92mmartin: Goodbye!\033[0m")
                logger.info("chat_end reason=quit")
                break
            logger.info("chat_input len=%d", len(user_input))

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
                    print("Commands: /help, /clear, /status, /memory, /history, /palette, /files, /open <path>:<line>, /worklog, /clock in|out, /privacy on|off|status, /keys, /retry, /onboarding, /context [refresh], /plan, /outputs [ledger|export <path>|search <text>], /export session <path>, /resume, /librarian inbox|request <topic>|sources <topic>|accept <n>|dismiss <n>, /rag status, /tasks add|list|done <n>, /review on|off, /abilities, /resources, /resource <path>, /tests, /rerun [command|test], /agent on|off|status, /cloud on|off, /ask <q>, /ingest <path>, /compress, /signoff, /exit")
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
                        if preview_write(Path(out_path), content):
                            Path(out_path).write_text(content, encoding="utf-8")
                            print(f"martin: Exported session to {out_path}")
                        else:
                            print("martin: Export cancelled.")
                    except Exception as e:
                        print(f"martin: Export failed ({e})")
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
                        context_cache = gather_context(Path.cwd(), max_recent=int(cfg.get("context", {}).get("max_recent", 10)))
                        st = load_state()
                        st["context_cache"] = context_cache
                        save_state(st)
                        chat_ui.print_context_summary(context_cache)
                        return True
                    if not context_cache:
                        from researcher.context_harvest import gather_context
                        context_cache = gather_context(Path.cwd(), max_recent=int(cfg.get("context", {}).get("max_recent", 10)))
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
                    except Exception:
                        pass
                    print(json.dumps(payload, ensure_ascii=False, indent=2))
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
            main_user = (
                "Context (do not repeat):\n"
                f"{json.dumps({'user_intent': step_details.get('user_intent_summary'), 'capability_inventory': step_details.get('inventory', []), 'snapshot': step_details.get('snapshot', {}), 'system': sys_ctx, 'memory': mem_ctx, 'last_command': last_cmd_summary}, ensure_ascii=False, indent=2)}\n\n"
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
                "If behavior = build/run/diagnose, output precise steps only if truly warranted.\n"
                "If behavior = review, focus on bugs, risks, regressions, and missing tests; be concise and specific.\n"
                "If behavior = review, format output with sections: Findings, Questions, Tests. Use bullets under Findings.\n"
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
            bot_json = _post_responses(payload, label="Main") # Use llm_utils's post_responses
            bot_response = _extract_output_text(bot_json) or ""
            interaction_history.append("martin: " + bot_response)

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
            if review_mode and "command:" not in (bot_response or "").lower():
                bot_response = _format_review_response(bot_response)

            print(f"\033[92mmartin:\n{bot_response}\033[0m")
            transcript.append("martin: " + bot_response)
            session_transcript.append("martin: " + bot_response)
            try:
                if not _privacy_enabled():
                    st = load_state()
                    st["session_memory"] = {"transcript": session_transcript[-200:]}
                    save_state(st)
            except Exception:
                pass

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
            if terminal_commands:
                print("\033[96mmartin: Rationale:\033[0m " + (user_input.strip() or "requested action"))
                print("\n\033[96mmartin: Proposed command plan (review):\033[0m")
                risk_info = []
                for c in terminal_commands:
                    risk = classify_command_risk(c, command_allowlist, command_denylist)
                    risk_info.append(risk)
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
                    output_path = ""
                    if output:
                        stored = _store_long_output(output, "cmd") if not _privacy_enabled() else ""
                        display = _format_output_for_display(output)
                        print(display)
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
                                import re
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
                save_state(st)
            except Exception:
                pass
        _mo_exit_check()
        _prompt_clock("Clock-out")

    finally:
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
    raise SystemExit(main())
