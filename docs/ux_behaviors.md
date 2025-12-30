# UX Behaviors Inventory

This document enumerates the UX behaviors and capabilities Martin exhibits in the CLI.

Behavior inventory (operator-visible)
- Plan/approve/execute: extracts `command:` lines into a plan, shows rationale, risk tags, and approvals.
- Follow-ups: short confirmations resolve to the active goal (see `/goal`).
- Context continuity: context pack + active context block used each turn.
- Review mode: `/review on` enforces Findings/Questions/Tests format.
- Summaries: end-of-turn summary after plan execution (what happened / what's next).
- Task chaining: when no manual tasks exist, command plans seed the task queue and auto-advance on success.
- Privacy mode: `/privacy on` disables transcript and ledger logging.
- Codex-style cadence: concise actions, explicit assumptions, and minimal questions unless blocked.

Process stacking + live updates
- Plan execution loop: per-command status with OK/FAIL and output capture.
- Fix loop: optional diagnose -> proposed fix commands -> edit/approve.
- Background notes: Librarian notifications surface in the inbox.
- Streaming: local LLM can stream tokens for `ask` when enabled.
- Status banner: workspace status and active goal can be shown in the footer.

Tooling capability catalog
- Internal abilities: `martin.<ability>` (env check, plan.extract_commands, dev.create_file append-only, diagnose).
- RAG operations: `/ask`, `/ingest`, `/rag status`, Librarian inbox + sources.
- Files: `/files`, `/open`, `/resource`, `/resources`, `/outputs`.
- Session: `/resume`, `/export session`, `/import session`, `/history`, `/worklog`.
- Remote: `/host`, `/remote` (tunnel control + per-host config).

Test/verify workflow
- `/verify` reports venv, pytest availability, scripts, and remote config validation.
- `scripts\install_martin.ps1` boots venv + shim (supports `-SkipDeps`).
- `scripts\run_tests.ps1` installs deps + runs pytest (supports `-SkipInstall`).
- `docs\verification.md` is the clean-machine checklist.
