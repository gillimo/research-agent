Supervisor And Oversight
========================

Purpose
- Provide a lightweight, local-only oversight loop for coding agents.
- Detect idle sessions and prompt for continuation without running unsafe actions.

Current Behavior
- `researcher nudge` checks the ledger at `logs/researcher_ledger.ndjson`.
- If the ledger is missing, it falls back to `logs/local.log` timestamps.
- The nudge message includes the last event name (if available) and idle time.

Guardrails
- Never auto-runs commands from nudge alone.
- Uses idle detection as a prompt signal, not a directive.

Configuration
- Idle threshold is configurable via `--idle-seconds` on the `nudge` command.
- Future expansion can add configurable prompts via config/env.

Next Steps (planned)
- Supervisor loop that re-prompts stalled agent runs.
- Configurable prompt templates for different agent types.
