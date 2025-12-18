import argparse
import os
import sys
from pathlib import Path
from typing import List

from rich.console import Console
from rich.table import Table

from researcher import sanitize
from researcher.config_loader import load_config, ensure_dirs
from researcher.index import SimpleIndex, FaissIndex
from researcher.ingester import ingest_files
from researcher.log_utils import setup_logger, log_event
from researcher.provenance import build_response
from researcher.answer import compose_answer
from researcher.martin_behaviors import sanitize_and_extract, run_plan
from researcher.supervisor import nudge_message
from researcher.local_llm import run_ollama_chat
from researcher.cloud_bridge import call_cloud


def read_prompt(args: argparse.Namespace) -> str:
    if args.stdin:
        return sys.stdin.read().strip()
    return " ".join(args.prompt or []).strip()


def _load_index(cfg):
    vs = cfg.get("vector_store", {}) or {}
    index_path = Path(vs.get("index_path", "data/index/mock_index.pkl"))
    mock_path = Path(vs.get("mock_index_path", "data/index/mock_index.pkl"))
    idx_type = vs.get("type", "simple")
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


def cmd_status(cfg) -> int:
    idx = _load_index(cfg)
    vs = cfg.get("vector_store", {}) or {}
    console = Console()
    table = Table(title="Status")
    table.add_column("field", style="cyan")
    table.add_column("value", style="white")
    rows = [
        ("local_model", str(cfg.get("local_model"))),
        ("embedding_model", str(cfg.get("embedding_model"))),
        ("index_type", vs.get("type", "simple")),
        ("index_path", str(vs.get("index_path", ""))),
        ("index_docs", str(idx.stats().get("count"))),
    ]
    for k, v in rows:
        table.add_row(k, v)
    console.print(table)
    return 0


def cmd_ingest(cfg, paths: List[str]) -> int:
    if not paths:
        print("No files provided to ingest.", file=sys.stderr)
        return 1
    ensure_dirs(cfg)
    logger = setup_logger(Path(cfg.get("data_paths", {}).get("logs", "logs")) / "local.log")
    vs = cfg.get("vector_store", {}) or {}
    idx_path = Path(vs.get("index_path", "data/index/mock_index.pkl"))
    mock_path = Path(vs.get("mock_index_path", "data/index/mock_index.pkl"))
    idx = _load_index(cfg)
    files = [Path(p) for p in paths if Path(p).exists()]
    result = ingest_files(idx, files)
    # persist index
    if isinstance(idx, FaissIndex):
        idx.save()
    else:
        idx.save(mock_path)
    log_event(logger, f"ingest files={len(files)} errors={len(result['errors'])} idx_type={vs.get('type','simple')}")
    for err in result["errors"]:
        print(f"error: {err}", file=sys.stderr)
    print(f"Ingested {result['ingested']} files into {idx_path}")
    return 0


def cmd_ask(cfg, prompt: str, k: int, use_llm: bool = False, cloud_mode: str = "off", cloud_cmd: str = "") -> int:
    ensure_dirs(cfg)
    logger = setup_logger(Path(cfg.get("data_paths", {}).get("logs", "logs")) / "local.log")
    vs = cfg.get("vector_store", {}) or {}
    idx_path = Path(vs.get("index_path", "data/index/mock_index.pkl"))
    idx = _load_index(cfg)
    sanitized, changed = sanitize.sanitize_prompt(prompt)
    hits = idx.search(sanitized, k=k)
    log_event(logger, f"ask k={k} hits={len(hits)} sanitized={changed}")
    answer = compose_answer(hits)
    cloud_hits = []
    # Optional local LLM generation
    llm_answer = None
    if cfg.get("local_llm_enabled") or use_llm:
        ctx = "\n".join([meta.get("chunk", "") for _, meta in hits][:3])
        llm_prompt = f"Context:\n{ctx}\n\nUser question:\n{prompt}\n\nAnswer concisely. If no context, say so."
        llm_answer = run_ollama_chat(cfg.get("local_model", "phi3"), llm_prompt, cfg.get("ollama_host", "http://localhost:11434"))
        log_event(logger, f"ask llm_used={bool(llm_answer)}")
        if llm_answer:
            answer = llm_answer

    # Optional cloud hop
    cloud_cfg = cfg.get("cloud", {}) or {}
    effective_cloud_cmd = cloud_cmd or cloud_cfg.get("cmd_template") or os.environ.get("CLOUD_CMD", "")
    if cloud_mode != "off":
        cloud_logs_root = Path(cfg.get("data_paths", {}).get("logs", "logs")) / "cloud"
        result = call_cloud(prompt, effective_cloud_cmd, cloud_logs_root)
        log_event(logger, f"ask cloud_mode={cloud_mode} rc={result.rc} redacted={result.changed}")
        if result.ok and result.output:
            cloud_hits.append((0.0, {"path": "cloud", "chunk": result.output}))
        elif result.error:
            print(f"[cloud] {result.error}", file=sys.stderr)

    resp = build_response("cli", answer=answer, hits=hits, logs_ref=str(idx_path), cloud_hits=cloud_hits)
    console = Console()
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
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="researcher CLI (skeleton)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", help="Show config summary")
    p_status.set_defaults(func=lambda cfg, args: cmd_status(cfg))

    p_ingest = sub.add_parser("ingest", help="Ingest files into index")
    p_ingest.add_argument("files", nargs="+", help="Files to ingest")
    p_ingest.set_defaults(func=lambda cfg, args: cmd_ingest(cfg, args.files))

    p_ask = sub.add_parser("ask", help="Ask the local index")
    p_ask.add_argument("prompt", nargs="*", help="Prompt text (or use --stdin)")
    p_ask.add_argument("--stdin", action="store_true", help="Read prompt from stdin")
    p_ask.add_argument("-k", type=int, default=5, help="Top-k results")
    p_ask.add_argument("--use-llm", action="store_true", help="Force local LLM generation (ollama)")
    p_ask.add_argument("--cloud-mode", choices=["off", "always"], default="off", help="Call cloud CLI after local retrieval")
    p_ask.add_argument("--cloud-cmd", default=os.environ.get("CLOUD_CMD", ""), help="Cloud command template with {prompt} placeholder")
    p_ask.set_defaults(func=lambda cfg, args: cmd_ask(cfg, read_prompt(args), args.k, use_llm=args.use_llm, cloud_mode=args.cloud_mode, cloud_cmd=args.cloud_cmd))

    add_plan_command(sub)
    add_supervise_command(sub)

    return parser


def main(argv: List[str] = None) -> int:
    cfg = load_config()
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(cfg, args)


def add_plan_command(sub):
    p_plan = sub.add_parser("plan", help="Extract and (optionally) run command plan from text")
    p_plan.add_argument("prompt", nargs="*", help="Text containing command: lines (or use --stdin)")
    p_plan.add_argument("--stdin", action="store_true", help="Read prompt from stdin")
    p_plan.add_argument("--run", action="store_true", help="Run extracted commands (non-interactive)")
    p_plan.add_argument("--timeout", type=int, default=120, help="Per-command timeout seconds")
    p_plan.set_defaults(func=handle_plan)


def handle_plan(cfg, args) -> int:
    logger = setup_logger(Path(cfg.get("data_paths", {}).get("logs", "logs")) / "local.log")
    prompt = read_prompt(args)
    sanitized, changed, cmds = sanitize_and_extract(prompt)
    print("Sanitized prompt:" if changed else "Prompt:", sanitized)
    if not cmds:
        print("No commands extracted.")
        return 0
    print("Command plan:")
    for i, c in enumerate(cmds, 1):
        print(f"  {i}. {c}")
    log_event(logger, f"plan extracted cmds={len(cmds)}")
    if args.run:
        results = run_plan(cmds, timeout=args.timeout)
        for cmd, rc, out in results:
            status = "OK" if rc == 0 else f"FAIL({rc})"
            print(f"[{status}] {cmd}\n{out}\n")
        log_event(logger, f"plan run cmds={len(cmds)}")
    return 0


def add_supervise_command(sub):
    p_sup = sub.add_parser("nudge", help="Check logs and print nudge if idle")
    p_sup.add_argument("--idle-seconds", type=int, default=300, help="Idle threshold")
    p_sup.set_defaults(func=handle_nudge)


def handle_nudge(cfg, args) -> int:
    logs_path = Path(cfg.get("data_paths", {}).get("logs", "logs")) / "local.log"
    msg = nudge_message(logs_path, idle_seconds=args.idle_seconds)
    print(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
