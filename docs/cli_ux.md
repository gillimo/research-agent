CLI UX Guide
============

Entry point
- Run from repo root with Python: `python -m researcher <command> [options]`.
- Commands: `status`, `ingest`, `ask`, `plan`, `nudge`, `chat`.
- Pipe-friendly: add `--stdin` to read prompt text from standard input.

Recommended flow
1) Status check: `python -m researcher status`  
   Shows local model, embedding model, index path/type, and doc count.
2) Ingest sample: `python -m researcher ingest data/sample/readme.txt`  
   Builds/updates the local index and sends a redacted ingest note to the Librarian (no raw file content).
3) Ask locally: `echo "your question" | python -m researcher ask --stdin [-k 5] [--use-llm]`  
   Prints a provenance table (score/source/chunk). `--use-llm` forces local LLM synthesis from retrieved context.
4) Plan/run commands: `echo "command: dir" | python -m researcher plan --stdin --run`  
   Shows sanitized prompt, extracted plan, and per-command status/output.
5) Nudge/oversight: `python -m researcher nudge [--idle-seconds 300]`  
   Emits a nudge if logs show idleness beyond the threshold.
6) Interactive chat: `python -m researcher chat`  
   If you mention a valid file path (or a Desktop filename) in chat, Martin will auto-ingest it (local, redacted note to Librarian) before answering.  
   Slash commands include `/history`, `/palette [pick <n>]`, `/files [pick <n>]`, `/open <path>:<line>`, `/worklog`, `/clock in|out`, `/privacy on|off|status`, `/keys`, `/retry`, `/onboarding`, `/rerun [command|test]`, `/outputs search <text>`, and `/context refresh`.  
   A workspace status banner summarizes git status and last command result.
7) TUI shell: `python -m researcher tui`  
   Keyboard-driven panels for palette, tasks, context, outputs, and process worklog (use `j/k` or arrows to navigate, `f` to filter outputs).

UX cues
- Tables use Rich for readable columns and headers.
- `ask` output shows provenance first (local hits), then stderr notes if sanitization altered input.
- `plan --run` reports `OK`/`FAIL(code)` per command with captured stdout/stderr.
- Dev flow previews diffs before applying generated code changes.
- Startup preflight prints a quickstart line with `/verify`, `/tests`, and `docs/tickets.md`.
- Startup includes a simple progress bar for preflight/clock-in/context/onboarding.
- Compact startup (`ui.startup_compact=true`) collapses preflight output to a single line and suppresses extra context chatter.
- Long responses show a live "Working: <stage> · request n/3" status line with a spinner above the input prompt.
- API retry bars are disabled by default; set `ui.api_progress=true` to show retry progress bars.
- Logs for all commands go to `logs/local.log` (rotating).
- Command approvals support inline (`inline`) and external (`editor`) editing options for multi-command edits.
- `/tests` shows the last-run status and supports `/tests run <n>` to execute a suggested command.
- Status banner includes model/provider info and local-only warnings.
- Outside-workspace commands prompt for confirmation before running.
- Startup context scan uses a fast mode outside git repos to avoid long hangs.
- Privacy mode (`/privacy on`) disables transcript and ledger persistence for the session.
- Cloud calls show a sanitized prompt preview and require approval before sending.
- Librarian inbox entries include trust scores and stale flags when available.
- Verbose logging can be enabled via `logging.verbose` in `config/local.yaml` (sanitized summaries only).
- Behavior toggles live under `behavior` in `config/local.yaml` (summaries, follow-up resolver, clarification policy, context block).
- Local LLM settings live under `local_llm` (enabled/streaming/fallbacks) and show in `/status`.
- Workspace boundary hard-block can be enforced with `execution.hard_block_outside`.
- Ingest policy lives under `ingest` (allowlist roots/exts and proprietary scan mode).
- Session import/export uses `/export session <path>` and `/import session <path>`.
- Device registry uses `/host list|pair <name>|use <name>`.
- Goal thread: `/goal status|set <text>|clear` manages the active goal used for follow-ups.
- Verification checklist: `/verify` reports venv/scripts/remote config status.
- Verification doc: see `docs/verification.md`.
- Behavior inventory: see `docs/ux_behaviors.md`.
- Expected behavior contract: see `docs/expected_behavior.md`.
- Redaction audit report: `/redaction report [days]`.
- Trust policy: `trust_policy.allow_cloud` can hard-disable cloud calls; `trust_policy.allow_librarian_notes` can suppress librarian inbox notes.
- Service helper: `scripts/martin_service.ps1 start|stop|status` manages the Librarian background process.
- Remote tunnel helper: `/remote start|stop|status` uses `remote_transport` config.
- Remote config overrides: `/remote config set <key> <value>` stores per-host overrides in state.
- Remote relay policy: set `execution.remote_policy=relay` to allow commands to run on the active host via SSH tunnel.
- Chat footer: `ui.footer=true` reprints the status banner after each turn for Codex-like readability.
- Export encryption: set `trust_policy.encrypt_exports=true` and `MARTIN_ENCRYPTION_KEY` to encrypt session/ledger exports.
- Log encryption: set `trust_policy.encrypt_logs=true` (or enable for remote) to write encrypted ledgers under `logs/secure/`.
- RAG trust labels: sources tagged `internal` or `public` and filtered via `trust_policy.allow_sources`.
- Key management: `/trust keygen`, `/encrypt <path>`, `/decrypt <path>`, `/rotate <path> <old_env> <new_env>`.

Optional cloud bridge (until integrated into `ask`)
- `echo "prompt" | python scripts/researcher_bridge.py --stdin --cloud-mode always --cloud-cmd "$env:CLOUD_CMD"`
- Set `CLOUD_CMD` to your preferred CLI (e.g., codex/gemini/llm) that accepts `{prompt}`.
- `CLOUD_CMD` must be a single command without pipes or redirection; it runs without a shell.
- Inline cloud hop from CLI: `echo "prompt" | python -m researcher ask --stdin --cloud-mode always --cloud-cmd "$env:CLOUD_CMD"` (sanitized, logged to `logs/cloud/cloud.ndjson`).

Notes
- Secrets stay in `.env` (gitignored). `OPENAI_API_KEY` only needed for the Martin artifact or future cloud hop.
- Local LLM defaults: Ollama `phi3` at `http://localhost:11434` (configurable in `config/local.yaml`).
