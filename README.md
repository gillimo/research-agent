Research Agent Workspace
========================

Two-agent system: a local "researcher" that owns the RAG store and a cloud assistant consulted through sanitized queries for breadth/recall.

Current stack
- Local model runtime: Ollama, with `phi3` (mini / mini-128k) already installed in `C:\Users\gilli\.ollama`.
- Embeddings/vector store: default `all-MiniLM-L6-v2` + FAISS index (`config/local.yaml`) with SimpleIndex fallback if model is unavailable.
- Interface: The `researcher` CLI now offers several subcommands for core functionality and an interactive `chat` mode. The `martin` launcher wraps `python -m researcher chat`.
  - Core commands: `status`, `ingest`, `ask`, `plan`, `nudge`, `abilities`, `resources`, `resource`, `supervise`.
  - Interactive chat: `python -m researcher chat` or `martin` (provides an interactive agent session).
  - Rich TUI shell: `python -m researcher tui` (keyboard-driven panels).
  - Cloud hops are integrated directly into `cmd_ask` and the interactive `chat` session based on configuration.
- Artifact: Sanitized Martin v5.1 reference at `martin_v5_1_reference.py` (requires `OPENAI_API_KEY` in `.env` for direct use).

Quick start
- Verify local model: `ollama list` should show `phi3`.
- Copy `.env.example` to `.env` and set `OPENAI_API_KEY` if you plan to use OpenAI models or features derived from Martin.
- **For Cloud integration:** Set `RESEARCHER_CLOUD_PROVIDER` (e.g., `openai`), `RESEARCHER_CLOUD_MODEL` (e.g., `gpt-4o`), and `RESEARCHER_CLOUD_API_KEY` (or reuse `OPENAI_API_KEY`) in your `.env` for cloud hops.
- **Local-only mode:** Set `local_only: true` in `config/local.yaml` (or `RESEARCHER_LOCAL_ONLY=1`) to disable all cloud calls.
- **Auto source discovery:** `auto_update.sources_on_gap: true` triggers Librarian source suggestions on low-confidence queries.
- Cloud logs are written to `logs/cloud/cloud.ndjson` when cloud calls run.
- **Researcher CLI usage:**
  - `python -m researcher --version`: Print CLI version.
  - `python -m researcher status [--json]`: Show config summary and current researcher state.
  - `python -m researcher status --simple-index`: Force SimpleIndex (skip FAISS).
  - `python -m researcher ingest data/sample/readme.txt [--json]`: Ingest documents into the local RAG.
  - `python -m researcher ingest data/sample/readme.txt --simple-index`: Ingest with SimpleIndex only.
  - `python -m researcher ingest data/sample --ext txt,md`: Ingest directories/globs with extension filtering.
  - `echo "query" | python -m researcher ask --stdin [-k 5] [--use-llm] [--cloud-mode auto --cloud-cmd "$env:CLOUD_CMD" --cloud-threshold 0.3] [--json]`: Ask the local index, with options for local LLM generation and cloud integration.
  - `echo "query" | python -m researcher ask --stdin --simple-index`: Ask with SimpleIndex only.
  - `python -m researcher plan --stdin [--run]`: Extract command plans and optionally run them.
  - `python -m researcher nudge`: Check agent activity.
  - `python -m researcher supervise --idle-seconds 300 --sleep-seconds 30`: Run a periodic supervisor loop.
  - `python -m researcher abilities`: List internal abilities (or run one by name).
  - `python -m researcher resources`: List readable resources under the repo root.
  - `python -m researcher resource <path>`: Read a resource under the repo root.
  - `python -m researcher abilities system.context`: Show safe system context data.
  - `python -m researcher serve --host 127.0.0.1 --port 8088`: Start local HTTP service.
  - `python -m researcher librarian status|start|shutdown`: Control the Librarian process.
- **Interactive Chat Session:**
  - `python -m researcher chat` or `martin`: Start a persistent interactive session with the researcher agent, leveraging Chef/Waiter orchestration, internal abilities, and smart command execution.
  - Slash commands: `/help`, `/clear`, `/status`, `/memory`, `/history`, `/palette [query|pick <n>]`, `/files [query|pick <n>]`, `/open <path>:<line>`, `/worklog`, `/clock in|out`, `/context [refresh]`, `/plan`, `/outputs`, `/export session <path>`, `/resume`, `/rag status`, `/tasks add|list|done <n>`, `/review on|off`, `/librarian inbox|request <topic>|sources <topic>|accept <n>|dismiss <n>`, `/abilities`, `/resources`, `/resource <path>`, `/tests`, `/agent on|off|status`, `/cloud on|off`, `/ask <q>`, `/ingest <path>`, `/compress`, `/signoff`, `/exit`.
  - Tests: `/tests run <n>` executes a suggested command and records status.
- **Auto-Update Configuration (in `config/local.yaml` or directly in code/env):**
  - `auto_update.ingest_threshold`: Define a `top_score` threshold (e.g., `0.1`) below which local retrievals will log a suggestion for ingesting more data.
  - `auto_update.ingest_cloud_answers`: Set to `true` to ingest successful cloud answers into the local RAG.
  - `vector_store.warm_on_start`: Set to `true` to pre-load the index when entering chat mode.
  - `rephraser.enabled`: Set to `true` to rephrase non-command chat replies.

Utilities
- `python scripts/ingest_demo.py`: Idempotent ingest demo (use `--simple-index` or `--no-clear` as needed).
- `python scripts/log_question.py --text "..."`: Log blockers in `logs/questions.ndjson`.
- `python scripts/legacy_import.py`: Scan for legacy PDFs and write a report to `docs/legacy_import_report.md`.

Project references
- `PROJECT_PLAN.md`: milestones and open decisions.
- `docs/architecture.md`: system diagram, data flow, and guardrails.
- `docs/setup_local.md`: local setup, model/embedding notes, and run commands.
- `docs/tickets.md`: ticket backlog including Codex parity audit (CX11-CX20).
