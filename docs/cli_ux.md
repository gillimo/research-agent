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
- Logs for all commands go to `logs/local.log` (rotating).
- Command approvals support inline (`inline`) and external (`editor`) editing options for multi-command edits.
- `/tests` shows the last-run status and supports `/tests run <n>` to execute a suggested command.
- Status banner includes model/provider info and local-only warnings.
- Outside-workspace commands prompt for confirmation before running.
- Privacy mode (`/privacy on`) disables transcript and ledger persistence for the session.

Optional cloud bridge (until integrated into `ask`)
- `echo "prompt" | python scripts/researcher_bridge.py --stdin --cloud-mode always --cloud-cmd "$env:CLOUD_CMD"`
- Set `CLOUD_CMD` to your preferred CLI (e.g., codex/gemini/llm) that accepts `{prompt}`.
- `CLOUD_CMD` must be a single command without pipes or redirection; it runs without a shell.
- Inline cloud hop from CLI: `echo "prompt" | python -m researcher ask --stdin --cloud-mode always --cloud-cmd "$env:CLOUD_CMD"` (sanitized, logged to `logs/cloud/cloud.ndjson`).

Notes
- Secrets stay in `.env` (gitignored). `OPENAI_API_KEY` only needed for the Martin artifact or future cloud hop.
- Local LLM defaults: Ollama `phi3` at `http://localhost:11434` (configurable in `config/local.yaml`).
