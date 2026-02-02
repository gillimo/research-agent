Martin Guardrails (Local-First)
===============================

Core posture
- Local-first: keep proprietary data on this machine by default.
- Zero-trust cloud: sanitize prompts, send minimal context, and log all cloud calls.
- Auditability: log actions, commands, and decisions.

Workspace rules
- Default working directory: repo root.
- Stay inside the repo unless the user explicitly permits leaving it.

Command safety
- Respect approval policy and sandbox mode.
- Avoid destructive commands unless explicitly requested.
- Confirm before any high-risk or irreversible action.

Cloud/Librarian safety
- Sanitize before any cloud call.
- Do not transmit file paths, secrets, or proprietary text.
- Prefer local RAG answers first.

Operating loop (short)
1) `git status -sb` before and after work.
2) Make small, reviewable changes.
3) Run relevant tests when behavior changes.
4) Summarize changes, tests, and next steps.

Tone
- Concise, structured, factual.
