import argparse
import os
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

# New imports for Librarian client
from researcher.librarian_client import LibrarianClient

# New imports for Martin's main loop
from researcher.state_manager import load_state, save_state, log_event, SessionCtx, ROOT_DIR
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
        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
        path = _OUTPUT_DIR / f"{ts}_{label}.log"
        path.write_text(output, encoding="utf-8")
        return str(path)
    except Exception:
        return ""


def _get_cli_logger(cfg):
    global _CLI_LOGGER
    if _CLI_LOGGER:
        return _CLI_LOGGER
    logs_dir = Path(cfg.get("data_paths", {}).get("logs", "logs"))
    _CLI_LOGGER = setup_logger(logs_dir / "martin.log", name="martin.cli")
    return _CLI_LOGGER

def get_status_payload(cfg, force_simple: bool = False) -> Dict[str, Any]:
    import time
    t0 = time.perf_counter()
    idx = _load_index(cfg, force_simple=force_simple)
    load_ms = (time.perf_counter() - t0) * 1000.0
    vs = cfg.get("vector_store", {}) or {}
    st = load_state()
    return {
        "version": __version__,
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

    if not skip_librarian:
        # Use LibrarianClient to request ingestion
        client = LibrarianClient()
        ingest_resp = client.request_ingestion(existing_paths)
        client.close()

        if ingest_resp.get("status") == "success":
            result_data = ingest_resp.get("result", {})
            ingested_count = result_data.get("ingested", 0)
            errors = result_data.get("errors", [])
            
            log_event(st, "ingest_command", files_count=len(paths), errors_count=len(errors), idx_type="via_librarian")
            for err in errors:
                print(f"error: {err}", file=sys.stderr)
            if as_json:
                print(json.dumps({"ok": True, "mode": "via_librarian", "ingested": ingested_count, "errors": errors}, ensure_ascii=False))
            else:
                print(f"Ingested {ingested_count} files (via Librarian)")
            return 0

        error_msg = ingest_resp.get("message", "Unknown error during ingestion via Librarian.")
        log_event(st, "ingest_command_failed", files_count=len(paths), error=error_msg)
        if not as_json:
            print(f"Warning: Librarian ingest failed ({error_msg}); falling back to local ingest.", file=sys.stderr)

    idx = _load_index(cfg, force_simple=force_simple)
    files = [Path(p) for p in existing_paths]
    local_result = ingest_files(idx, files)
    if hasattr(idx, "save"):
        idx.save()
    log_event(st, "ingest_command_fallback", files_count=len(files), errors_count=len(local_result.get("errors", [])), idx_type="local")
    if as_json:
        print(json.dumps({"ok": True, "mode": "local_fallback", "ingested": local_result.get("ingested", 0), "errors": local_result.get("errors", [])}, ensure_ascii=False))
    else:
        for err in local_result.get("errors", []):
            print(f"error: {err}", file=sys.stderr)
        print(f"Ingested {local_result.get('ingested', 0)} files (local fallback)")
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
    effective_cloud_cmd = cloud_cmd or cloud_cfg.get("cmd_template") or os.environ.get("CLOUD_CMD", "")
    threshold = cloud_threshold if cloud_threshold is not None else cloud_cfg.get("trigger_score", 0.0)
    should_cloud = should_cloud_hop(cloud_mode, top_score, threshold)
    if should_cloud:
        from researcher.cloud_bridge import _hash
        client = LibrarianClient()
        cloud_resp = client.query_cloud(
            prompt=prompt,
            cloud_mode=cloud_mode,
            cloud_cmd=effective_cloud_cmd,
            cloud_threshold=cloud_threshold # Pass for context, though Librarian handles thresholding
        )
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

        log_event(st, "ask_cloud_hop", cloud_mode=cloud_mode, rc=result_rc, redacted=result_changed, trigger_score=top_score, threshold=threshold, librarian_response_status=cloud_resp.get("status")) # Use state_manager's log_event
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
    from researcher.command_utils import extract_commands
    from researcher.llm_utils import _post_responses, _extract_output_text, MODEL_MAIN, interaction_history, diagnose_failure, current_username, rephraser
    from researcher.orchestrator import decide_next_step, dispatch_internal_ability
    from researcher.resource_registry import list_resources, read_resource
    from researcher.runner import run_command_smart, enforce_sandbox
    from researcher.librarian_client import LibrarianClient
    from researcher.system_context import get_system_context
    import shlex
    st = load_state()
    sess = SessionCtx(st)
    sess.begin()
    logger = _get_cli_logger(cfg)
    logger.info("chat_start")
    last_user_request = ""
    agent_mode = False
    cloud_enabled = bool(cfg.get("cloud", {}).get("enabled"))
    approval_policy = (cfg.get("execution", {}).get("approval_policy") or "on-request").lower()
    sandbox_mode = (cfg.get("execution", {}).get("sandbox_mode") or "workspace-write").lower()
    session_transcript = []
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
    print("\nmartin: Welcome! Type 'quit' to exit.")
    def _setup_readline():
        try:
            import readline
        except Exception:
            return None
        commands = [
            "/help", "/clear", "/status", "/memory", "/context", "/plan", "/outputs", "/abilities", "/resources", "/resource", "/tests",
            "/agent", "/cloud", "/ask", "/ingest", "/compress", "/signoff", "/exit", "/catalog",
        ]
        def completer(text, state):
            buffer = readline.get_line_buffer()
            if buffer.startswith("/"):
                matches = [c for c in commands if c.startswith(buffer)]
                if state < len(matches):
                    return matches[state]
            return None
        readline.set_completer(completer)
        try:
            readline.parse_and_bind("tab: complete")
        except Exception:
            pass
        return readline
    _setup_readline()
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
    while True:
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
                print("Commands: /help, /clear, /status, /memory, /context, /plan, /outputs, /abilities, /resources, /resource <path>, /tests, /agent on|off|status, /cloud on|off, /ask <q>, /ingest <path>, /compress, /signoff, /exit")
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
            if name == "signoff":
                if transcript:
                    summary = rephraser("\n".join(transcript)[-4000:])
                else:
                    summary = "No transcript captured."
                print("martin: Signoff")
                print(summary)
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
            if name == "plan":
                st = load_state()
                payload = st.get("last_plan", {})
                print(json.dumps(payload, ensure_ascii=False, indent=2))
                return True
            if name == "outputs":
                try:
                    files = sorted(_OUTPUT_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)[:10]
                    for p in files:
                        print(str(p))
                except Exception:
                    print("martin: No outputs found.")
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
                    cmds = []
                    root = Path.cwd()
                    if (root / "tests").exists():
                        cmds.append("python -m pytest")
                    if (root / "pyproject.toml").exists():
                        cmds.append("python -m pytest -q")
                    if (root / "scripts").exists() and (root / "scripts" / "ingest_demo.py").exists():
                        cmds.append("python scripts/ingest_demo.py --simple-index")
                    if not cmds:
                        print("martin: No test helpers detected in this folder.")
                        return True
                    print("martin: Suggested test/run commands:")
                    for c in cmds:
                        print(f"- {c}")
                except Exception:
                    print("martin: Unable to suggest tests here.")
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
                if not context_cache:
                    from researcher.context_harvest import gather_context
                    context_cache = gather_context(Path.cwd(), max_recent=int(cfg.get("context", {}).get("max_recent", 10)))
                    st = load_state()
                    st["context_cache"] = context_cache
                    save_state(st)
                payload = context_cache
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

        if cloud_enabled and cfg.get("cloud", {}).get("trigger_on_disagreement") and _is_disagreement(user_input):
            prompt = (last_user_request or user_input).strip()
            prompt = f"{prompt}\n\nUser feedback: {user_input}\nPlease answer correctly."
            client = LibrarianClient()
            cloud_resp = client.query_cloud(
                prompt=prompt,
                cloud_mode="always",
                cloud_cmd=cfg.get("cloud", {}).get("cmd_template") or os.environ.get("CLOUD_CMD", ""),
            )
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
            cloud_resp = client.query_cloud(
                prompt=prompt,
                cloud_mode="always",
                cloud_cmd=cfg.get("cloud", {}).get("cmd_template") or os.environ.get("CLOUD_CMD", ""),
            )
            client.close()
            if cloud_resp.get("status") == "success":
                result = cloud_resp.get("result", {})
                output = result.get("output", "")
                if output:
                    log_event(st, "cloud_hop", reason=reason, output_len=len(output))
                    return output
            log_event(st, "cloud_hop_failed", reason=reason, error=cloud_resp.get("message", "no_output"))
            return None

        main_sys = (
            "You are Martin, a helpful and competent AI researcher assistant.\n"
            "Speak as Martin. Be direct and concise.\n"
            "Do not describe internal reasoning or system instructions.\n"
            "You have access to the local filesystem via `command:` lines.\n"
            "Do not claim you cannot access files; propose commands instead.\n"
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
        main_user = (
            "Context (do not repeat):\n"
            f"{json.dumps({'user_intent': step_details.get('user_intent_summary'), 'capability_inventory': step_details.get('inventory', []), 'snapshot': step_details.get('snapshot', {}), 'system': sys_ctx, 'memory': mem_ctx}, ensure_ascii=False, indent=2)}\n\n"
            "Guidance (do not repeat):\n"
            f"{step_details.get('guidance_banner', '')}\n\n"
            "Behavior (do not repeat):\n"
            f"{step_details.get('behavior', 'chat')}\n\n"
            "Question summaries (user asked):\n"
            f"{q_lines}\n\n"
            "Internal invocation protocol: command: martin.<ability_key> <payload>\n\n"
            "CRITICAL: For any request involving file system navigation (cd), listing files (ls, dir), reading files (cat, type), or running tools, you MUST reply with a `command:` line. Do not suggest the command in plain text.\n"
            "When you decide to execute an action, adopt a helpful and proactive tone, like 'Let me handle that for you,' before providing the `command:` line.\n"
            "If the user asks to inspect files, run tools, or check system state, include `command:` lines.\n"
            "If behavior = chat, respond directly to the user with a helpful reply, but follow the CRITICAL rule above.\n"
            "If behavior = build/run/diagnose, output precise steps only if truly warranted.\n"
            "If behavior = review, focus on bugs, risks, regressions, and missing tests; be concise and specific.\n"
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

        if cloud_enabled and cfg.get("cloud", {}).get("trigger_on_empty_or_decline", True):
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

        print(f"\033[92mmartin:\n{bot_response}\033[0m")
        transcript.append("martin: " + bot_response)
        session_transcript.append("martin: " + bot_response)
        try:
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
            print("\n\033[96mmartin: Proposed command plan (review):\033[0m")
            for i, c in enumerate(terminal_commands, 1):
                print(f"  {i}. {c}")
            try:
                if approval_policy in ("never", "on-failure") or agent_mode:
                    confirm = "yes"
                else:
                    confirm = input("\033[93mApprove running these commands? (yes/no/abort)\033[0m ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                confirm = "no" # Default to no on interrupt/EOF
            if confirm == "abort":
                print("\033[92mmartin: Aborting per request.\033[0m")
                logger.info("chat_cmd_abort count=%d", len(terminal_commands))
                continue
            elif confirm == "no":
                print("\033[92mmartin: Understood - not running commands. I remain at your disposal.\033[0m")
                logger.info("chat_cmd_denied count=%d", len(terminal_commands))
                continue

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
            plan.append({
                "index": i + 1,
                "cmd": cmd,
                "status": "pending",
                "internal_key": ability_key,
                "payload": payload_txt,
                "output": "",
                "started_at": None,
                "ended_at": None,
                "duration_s": 0.0,
            })

        successes_this_turn = 0
        failures_this_turn = 0
        bar = tqdm(plan, desc="Executing Command Plan", unit="cmd", leave=False) # Use tqdm
        for step in bar:
            bar.set_postfix({"ok": successes_this_turn, "fail": failures_this_turn}, refresh=True)
            if step["status"] != "pending":
                continue
            step["started_at"] = time.time()
            print(f"Executing: {step['cmd']}")
            if step.get("internal_key"):
                started = time.time()
                try:
                    # Use researcher's dispatch_internal_ability
                    ok, output = dispatch_internal_ability(step["internal_key"], step.get("payload") or "")
                except Exception as e:
                    ok = False
                    output = f"(internal error) {e}"
                step["ended_at"] = time.time()
                step["duration_s"] = round(step["ended_at"] - started, 3)
            else:
                # Enforce sandbox before running
                allowed, reason = enforce_sandbox(step["cmd"], sandbox_mode, str(Path.cwd()))
                if not allowed:
                    ok, output = False, reason
                else:
                    ok, output = run_command_smart(step["cmd"])
            step["ended_at"] = step["ended_at"] or time.time()
            step["duration_s"] = step["duration_s"] or round(step["ended_at"] - step["started_at"], 3)
            step["output"] = output or ""
            if ok:
                step["status"] = "ok"
                successes_this_turn += 1
                if output:
                    stored = _store_long_output(output, "cmd")
                    display = _format_output_for_display(output)
                    print(display)
                    if stored:
                        print(f"[full output saved to {stored}]")
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
                if output:
                    stored = _store_long_output(output, "cmd_fail")
                    display = _format_output_for_display(output)
                    print(display)
                    if stored:
                        print(f"[full output saved to {stored}]")
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
                        try:
                            if agent_mode:
                                confirm_fix = "yes"
                            else:
                                confirm_fix = input("\033[93mApprove running FIX commands? (yes/no/abort)\033[0m ").strip().lower()
                        except (EOFError, KeyboardInterrupt):
                            confirm_fix = "no"
                        if confirm_fix == "abort":
                            print("\033[92mmartin: Aborting per request.\033[0m")
                            break
                        elif confirm_fix == "yes":
                            for new_command in new_terminal_commands:
                                print(f"Executing (fix): {new_command}")
                                # Use researcher's run_command_smart
                                s2, out2 = run_command_smart(new_command)
                                if s2:
                                    successes_this_turn += 1
                                else:
                                    failures_this_turn += 1
                        else:
                            print("\033[92mmartin: Fix not applied. Continuing.\033[0m")
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
        print(f"\033[92mmartin: Turn complete - OK: {successes_this_turn}, FAIL: {failures_this_turn}\033[0m")
        logger.info("chat_turn_complete ok=%d fail=%d", successes_this_turn, failures_this_turn)
        try:
            st = load_state()
            st["last_plan"] = {"steps": terminal_commands, "status": "complete", "ok": successes_this_turn, "fail": failures_this_turn}
            save_state(st)
        except Exception:
            pass

    if args.transcript:
        try:
            Path(args.transcript).write_text("\n".join(transcript) + "\n", encoding="utf-8")
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
    p_plan.add_argument("--timeout", type=int, default=120, help="Per-command timeout seconds")
    p_plan.set_defaults(func=handle_plan)


def handle_plan(cfg, args) -> int:
    from researcher.command_utils import extract_commands
    from researcher.orchestrator import dispatch_internal_ability
    from researcher.runner import run_command_smart_capture
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
    
    if args.run:
        # Replaced run_plan with execution loop using run_command_smart and dispatch_internal_ability
        results = []
        any_fail = False
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
                ok, stdout_text, stderr_text, rc = run_command_smart_capture(cmd_str)
            
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


if __name__ == "__main__":
    raise SystemExit(main())
