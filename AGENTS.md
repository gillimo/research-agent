# AGENTS.md

This file defines how to run Martin (the local coding/research agent) in this project.

## Identity
- The agent is named **Martin**.
- Martin is the primary user-facing assistant. The Librarian supports Martin.
- Use `martin` (or `researcher`) CLI to interact.

## How to run
- Start interactive chat:
  - `martin`
  - `martin chat`
- Start TUI shell:
  - `martin tui`
- One-shot ask:
  - `echo "question" | martin ask --stdin`
- Status and utilities:
  - `martin status`
  - `martin abilities`
  - `martin supervise`

## Slash commands (chat)
- `/help` show commands
- `/agent on|off|status` toggles agent mode (auto-approve commands)
- `/cloud on|off` toggles cloud usage (if configured)
- `/status` prints JSON status
- `/memory` prints memory + history
- `/context` prints repo context (git status/diff/recent files)
- `/plan` prints last plan state
- `/outputs` lists recent saved long outputs (`/outputs ledger` shows tool ledger; `/outputs export <path>` writes JSON)
- `/resume` restores the latest saved session snapshot
- `/librarian inbox` lists recent Librarian notes
- `/librarian request <topic>` asks the Librarian for a sanitized research note
- `/librarian sources <topic>` asks the Librarian for public source suggestions
- `/librarian accept <n>` ingests a Librarian note summary into local RAG
- `/librarian dismiss <n>` removes an inbox item without ingesting
  - If the inbox item is a RAG gap suggestion, `/librarian accept <n>` triggers a research request.
- `/tasks add|list|done <n>` manages a simple task queue
- `/review on|off` toggles review mode (bugs/risks/tests focus)
- `/export session <path>` writes a JSON bundle (transcript tail, context, tasks, tool ledger)
- `/rag status` shows inbox count, recent RAG gaps, and last ingest metadata
- `/ask <q>` ask local RAG once
- `/ingest <path>` ingest quickly (local)
- `/context refresh` forces a new context pack and prints a brief summary
- `/history pick <n>` selects a recent input for reuse
- `/palette [query|pick <n>]` shows slash commands + recent inputs and can select an entry
- `/files [query|pick <n>]` lists repo files and can select an entry
- `/compress` summarize transcript
- `/signoff` produce signoff summary
- `/exit` or `/quit` leave chat

## Agent mode
- When agent mode is ON, Martin auto-approves commands and fix steps.
- Use `/agent off` to restore manual confirmations.
- Agent mode bypasses manual confirmations even when approval_policy is `on-request`; use with caution.

## Execution controls
Configured in `config/local.yaml`:
- `execution.approval_policy`: `on-request|on-failure|never`
- `execution.sandbox_mode`: `read-only|workspace-write|full`

## Memory
- Session transcript is kept during chat and archived on exit.
- Last path/listing memory is stored for context across sessions.
- Use `/memory` to view.
- Context harvesting can be enabled in `config/local.yaml` via `context.auto`.

## Cloud/Librarian
- Cloud is optional. Enable via `cloud.enabled` and env vars.
- Librarian can be started/stopped with `martin librarian start|status|shutdown`.

## Logs
- CLI log: `logs/martin.log`
- Cloud log: `logs/cloud/cloud.ndjson`

## Logbook
- Clock-ins and sign-ins live in `docs/logbook.md` (append newest first).

## Workflow (formal)
1) Check `git status -sb` before and after work.
2) Review `docs/tickets.md` and align on next priorities.
3) Make changes in small, reviewable chunks.
4) Log bugs in `docs/bug_log.md` when found (and close resolved items).
5) Update docs for any UX/behavior changes.
6) Run tests or relevant checks for changed areas.
7) Write a logbook entry in `docs/logbook.md` (clock-in and clock-out).
8) Run `/signoff` or provide a signoff summary when finishing a session.
9) Summarize changes, test results, and next steps.
