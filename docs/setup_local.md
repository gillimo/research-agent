Setup (Local)
=============

Prereqs
- Windows with PowerShell.
- Ollama installed (present) and `phi3` downloaded (`ollama list` to verify).
- Python 3.11+ recommended for the CLI/service (will add requirements once code lands).
- Secrets/config: copy `.env.example` to `.env` and set `OPENAI_API_KEY` (required for Martin artifact/cloud), `MARTIN_MODEL_MAIN`, `MARTIN_MODEL_MINI` as needed.
- Optional: Hugging Face token only if you switch to gated embedding models; default embedding is public (`all-MiniLM-L6-v2`).
- Local-only mode: set `local_only: true` in `config/local.yaml` or `RESEARCHER_LOCAL_ONLY=1` to block all cloud calls.
- Auto source discovery: set `auto_update.sources_on_gap: true` to request Librarian source suggestions on low-confidence queries.

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
- SimpleIndex only: add `--simple-index` to `status`, `ingest`, or `ask` to skip FAISS and embeddings.
- Ingest filtering: `python -m researcher ingest data/sample --ext txt,md --max-files 100` (dirs/globs supported).
- Default vector store uses FAISS; if HF model download fails or format mismatches, CLI falls back to SimpleIndex (`mock_index_path`). To avoid 401s, keep default embedding or provide HF auth for private models.
- Logs: `logs/local.log` (rotating) captures ask/ingest/plan/nudge activity.
 - Cloud logs: `logs/cloud/cloud.ndjson` captures cloud call events (hashes/redaction flags).
 - Supervisor loop: `python -m researcher supervise --idle-seconds 300 --sleep-seconds 30` to emit idle prompts.
- Slash commands: `/help`, `/clear`, `/status`, `/memory`, `/history`, `/palette [query|pick <n>]`, `/files [query|pick <n>]`, `/open <path>:<line>`, `/worklog`, `/clock in|out`, `/privacy on|off|status`, `/keys`, `/retry`, `/onboarding`, `/context [refresh]`, `/plan`, `/outputs [ledger|export|search]`, `/abilities`, `/resources`, `/resource <path>`, `/tests`, `/rerun [command|test]`, `/agent on|off|status`, `/cloud on|off`, `/ask <q>`, `/ingest <path>`, `/compress`, `/signoff`, `/exit`.
- Privacy: `/privacy on|off|status` disables transcript/log persistence for the current session.
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
