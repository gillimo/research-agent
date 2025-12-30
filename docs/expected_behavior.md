# Expected Behavior (Martin CLI)

This document defines how Martin is expected to behave during normal operation. It is the behavioral contract used for QA, UAT, and parity checks.

Core interaction flow
- Startup prints context summary and status banner; warns if local-only is on with cloud creds present.
- Clock-in/out entries are recorded automatically without prompting for a user note.
- Minimal questions: Martin proceeds unless blocked by policy or ambiguity; assumptions are stated explicitly.
- Follow-ups like "do it", "continue", "yes" resolve to the active goal when set.

Command planning and execution
- Any response containing `command:` lines triggers a plan with rationale, risk tags, and approval.
- Commands are gated by approval_policy and sandbox_mode; blocked items are skipped with explanation.
- High-risk commands require explicit YES confirmation.
- Failed commands record a retry hint and offer a fix-command review loop.

Task continuity
- If no manual tasks exist, a command plan seeds the task queue.
- Successful commands advance the auto-generated task list.
- `/tasks` always shows the current queue and next action.

Context continuity
- Active goal persists across turns until cleared (`/goal clear`).
- Active context block includes goal, next action, last plan status, and last command summary.
- `/context` refreshes the context pack and prints a concise summary.

Logging and privacy
- Normal sessions log tool calls, plans, and outputs with redaction.
- `/privacy on` disables transcript and ledger logging for the session.
- Local-only mode blocks all cloud calls; attempted calls are logged as denied.

Librarian behavior
- Inbox notifications are surfaced on turn start.
- Notes include trust labels and are blocked when policy disallows them.
- `/librarian status --verbose` reports IPC health and last error.

UX expectations
- Output cadence follows: rationale -> plan -> execution -> results -> summary.
- Footer banner (when enabled) remains visible and does not obscure scrollback.
- TUI mode offers palette, tasks, outputs, and context panels with key hints.

Codex parity behaviors
- Be concise and action-first; avoid extra questions unless blocked.
- State assumptions explicitly when proceeding without clarification.
- Prefer command plans with approvals; include a short rationale for each plan.
- Show a short "what I did / what's next" summary after tool runs.
- Suggest tests or next steps when they are natural and low-risk.
- Use structured review format in review mode (Findings/Questions/Tests).
- Keep context continuity: goal + next action always visible and updated.
- Never log secrets; redact paths/tokens in summaries and logs.

Testing/verification
- `python -m pytest -q` is the primary test gate.
- `/verify` reports setup gaps and next steps.
- UAT harness can run scripted sessions via stdin or test socket.

UAT socket harness (optional)
- Enable with `MARTIN_TEST_SOCKET=1` or scenario `use_socket: true`.
- Harness reads CLI output events and feeds inputs over TCP.
- Mailbox mode is async: fire inputs, log NDJSON events, then review results.
- Test socket mode is transparent: it only provides I/O transport and does not change behavior or feature flow.

Known exits
- `quit` or Ctrl+C exits cleanly and writes clock-out notes (unless skipped).
