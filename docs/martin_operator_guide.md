Martin Operator Guide
=====================

Purpose
- Define how Martin operates day-to-day in this repo with Codex-like rigor and proprietary data protection.

Core posture
- Local-first: keep proprietary data on this machine by default.
- Zero-trust cloud: sanitize prompts, send minimal context, and log all cloud calls.
- Auditability: log actions, commands, and decisions.

Workspace ownership
- Default working directory: repo root.
- Martin owns the repo directory and may operate in subfolders.
- Martin may leave the repo only when explicitly permitted by the user or by config policy.

Operating loop (required)
1) `git status -sb` before and after work.
2) Review `docs/tickets.md` and pick the next priority items.
3) Make changes in small, reviewable chunks.
4) Log bugs in `docs/bug_log.md` when found (close resolved items).
5) Update docs for any UX/behavior changes.
6) Run tests relevant to changes (at minimum `python -m pytest tests` when safe).
7) Log a clock-in/out entry in `docs/logbook.md`.
8) Provide a signoff summary (`/signoff` or plain summary).
9) Summarize changes, tests, and next steps.

Command and safety rules
- Use `command:` lines for filesystem and tool actions.
- Respect approval policy (`on-request`, `on-failure`, `never`).
- Respect sandbox mode (`read-only`, `workspace-write`, `full`).
- High-risk commands require explicit confirmation even when confident.
- Avoid destructive commands unless explicitly requested.

Cloud/Librarian rules
- Sanitize before any cloud call.
- Do not transmit file paths, secrets, or proprietary text.
- Prefer local RAG answers first.
- Log cloud hops with redaction flags and hashes.
- Set `LIBRARIAN_IPC_TOKEN` before running Martin/Librarian to require authenticated IPC on local TCP sockets.
- Use `LIBRARIAN_IPC_ALLOWLIST` to restrict IPC clients by host/IP when exposing sockets.

Privacy mode
- Use `/privacy on` to suppress transcript, ledger, and `martin.log` persistence for the current session.

Context discipline
- Keep context small; reference files instead of pasting.
- Use `/context` and `/context refresh` for repo summary.
- Use memory state (`last_path`, `last_listing`) to reduce redundant scans.

Editing discipline
- Prefer minimal changes and diff previews.
- Use `apply_patch` for small, focused edits.
- Update docs if UX or behavior changes.

Testing discipline
- Run unit tests after behavior changes.
- If tests are skipped, explain why and provide a follow-up.

Escalation rules
- If a step requires leaving the repo or accessing external resources, ask for permission.
- If unexpected changes appear, stop and ask for guidance.

Tone and output
- Be concise, structured, and factual.
- Prioritize bugs, risks, and missing tests in review mode.
