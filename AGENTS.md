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
- `/outputs` lists recent saved long outputs
- `/ask <q>` ask local RAG once
- `/ingest <path>` ingest quickly (local)
- `/compress` summarize transcript
- `/signoff` produce signoff summary
- `/exit` or `/quit` leave chat

## Agent mode
- When agent mode is ON, Martin auto-approves commands and fix steps.
- Use `/agent off` to restore manual confirmations.

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
