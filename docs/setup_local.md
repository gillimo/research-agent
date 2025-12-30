Setup (Local)
=============

Prereqs
- Windows with PowerShell.
- Ollama installed (present) and `phi3` downloaded (`ollama list` to verify).
- Python 3.11+ recommended for the CLI/service (will add requirements once code lands).
- Secrets/config: copy `.env.example` to `.env` and set `OPENAI_API_KEY` (required for Martin artifact/cloud), `MARTIN_MODEL_MAIN`, `MARTIN_MODEL_MINI` as needed.
- Optional: Hugging Face token only if you switch to gated embedding models; default embedding is public (`all-MiniLM-L6-v2`).
- Local-only mode (default): `local_only: true` in `config/local.yaml` or `RESEARCHER_LOCAL_ONLY=1` blocks all cloud calls. Set `local_only: false` to enable cloud.
- Auto source discovery: set `auto_update.sources_on_gap: true` to request Librarian source suggestions on low-confidence queries.
- IPC auth (recommended): set `LIBRARIAN_IPC_TOKEN` in the environment for both Martin and the Librarian to require authenticated TCP messages.
- IPC allowlist (optional): set `LIBRARIAN_IPC_ALLOWLIST=127.0.0.1,::1` to limit IPC clients by host/IP.
- IPC limits (optional): set `LIBRARIAN_IPC_MAX_BYTES` and `LIBRARIAN_IPC_CHUNK_BYTES` to cap message size and chunk large ingest payloads.
- IPC retention (optional): set `LIBRARIAN_INBOX_MAX` and `LIBRARIAN_INBOX_RETENTION_DAYS` to cap inbox size and age.
- Librarian policy (optional): set `LIBRARIAN_TOPIC_BLOCKLIST` (comma-separated) and `LIBRARIAN_SOURCE_STALE_DAYS` to block topics and flag stale sources.
- Librarian gap rate limits (optional): set `LIBRARIAN_GAP_DEDUPE_S`, `LIBRARIAN_GAP_MAX_PER_TOPIC`, and `LIBRARIAN_GAP_WINDOW_S`.

Environment layout
- Clone/open `C:\Users\gilli\OneDrive\Desktop\research_agent`.
- Data dirs (create as needed): `data/raw`, `data/processed`, `data/index`, `logs`.
- Config: `config/local.yaml` for model/provider choices; secrets via env vars.

Suggested Python env
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Model checks
```powershell
ollama list           # should show phi3
ollama run phi3 "hi"  # quick smoke test
```

Commands (current)
- Researcher CLI: `python -m researcher status`, `python -m researcher ingest data/sample/readme.txt`, `echo "test" | python -m researcher ask --stdin`, `python -m researcher plan --stdin --run`, `python -m researcher nudge`, `python -m researcher supervise`.
- Rich TUI: `python -m researcher tui`.
- Bridge (optional cloud hop): `$env:CLOUD_CMD='codex --model gpt-4o --prompt "{prompt}"' ; echo "question" | python scripts/researcher_bridge.py --stdin --cloud-mode always`
- Inline cloud hop: `echo "prompt" | python -m researcher ask --stdin --cloud-mode auto --cloud-cmd "$env:CLOUD_CMD" --cloud-threshold 0.3` (sanitized, logs to `logs/cloud/`; `--cloud-mode always` to force).
- `CLOUD_CMD` must be a single command without pipes or redirection; it runs without a shell.
- Cloud calls show a sanitized prompt preview and require approval before sending (unless approval_policy=never).
- SimpleIndex only: add `--simple-index` to `status`, `ingest`, or `ask` to skip FAISS and embeddings.
- Ingest filtering: `python -m researcher ingest data/sample --ext txt,md --max-files 100` (dirs/globs supported). Ingest is local; a redacted note is sent to the Librarian.
- Auto-ingest: if you mention a valid file path (or a Desktop filename) in chat, Martin ingests it automatically before answering.
- Default vector store uses FAISS; if HF model download fails or format mismatches, CLI falls back to SimpleIndex (`mock_index_path`). To avoid 401s, keep default embedding or provide HF auth for private models.
- Optional: set `RESEARCHER_FORCE_SIMPLE_INDEX=1` to force SimpleIndex and avoid embedding downloads.
- Logs: `logs/local.log` (rotating) captures ask/ingest/plan/nudge activity.
 - Cloud logs: `logs/cloud/cloud.ndjson` captures cloud call events (hashes/redaction flags).
 - Supervisor loop: `python -m researcher supervise --idle-seconds 300 --sleep-seconds 30` to emit idle prompts.
- Slash commands: `/help`, `/clear`, `/status`, `/memory`, `/history`, `/palette [query|pick <n>]`, `/files [query|pick <n>]`, `/open <path>:<line>`, `/worklog`, `/clock in|out`, `/privacy on|off|status`, `/keys`, `/retry`, `/onboarding`, `/context [refresh]`, `/plan`, `/outputs [ledger|export|search]`, `/abilities`, `/resources`, `/resource <path>`, `/tests`, `/rerun [command|test]`, `/agent on|off|status`, `/cloud on|off`, `/ask <q>`, `/ingest <path>`, `/compress`, `/signoff`, `/exit`.
- Privacy: `/privacy on|off|status` disables transcript, ledger, and `martin.log` persistence for the current session.
- Test runs: use `/tests run <n>` from the suggested list to execute and record status.
- Launcher install (Windows): `powershell -ExecutionPolicy Bypass -File scripts\\install_martin.ps1`  
  Uninstall: `powershell -ExecutionPolicy Bypass -File scripts\\uninstall_martin.ps1`.
- Starter RAG: `python -m researcher ingest docs/starter_rag --max-files 50`
- Additional: `/export session <path>`, `/resume`, `/rag status`, `/tasks add|list|done <n>`, `/review on|off`, `/librarian inbox|request <topic>|sources <topic>|accept <n>|dismiss <n>`.

Notes
- Embedding model/vector DB defaults are set in `config/local.yaml` (`all-MiniLM-L6-v2` + FAISS) with a SimpleIndex fallback.
- Cloud credentials (e.g., `CLOUD_MODEL`, `CLOUD_API_KEY`) will be read from env; do not commit keys.
- OPENAI_API_KEY is required for the Martin artifact (`martin_v1_4_7.py`) or any cloud hop; keep it in `.env` (ignored by git).
- `auto_update.ingest_cloud_answers`: when true, successful cloud answers are chunked and ingested into the local RAG.
 - `cloud.trigger_on_low_confidence`, `cloud.low_confidence_threshold`, `cloud.trigger_on_empty_or_decline`: control automatic cloud hops from chat.
 - `vector_store.warm_on_start`: when true, chat mode will pre-load the index.
 - `rephraser.enabled`: when true, chat replies without commands are rephrased for clarity.
