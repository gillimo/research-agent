# UX Test Audit Plan

This plan maps UX-related tickets to concrete verification checks. Use it after each behavior change or CLI UX update.

Latest results (2025-12-30)
- behavior_smoke: PASS (goal clear + agent on + /status; mailbox exits cleanly).
- behavior_review: PASS (Findings/Questions/Tests format + workspace default; approval handled).
- behavior_goal: PASS (plan runs on approval, goal clears after Done OK).
- behavior_timeout: PASS (mailbox exits during in-flight rerun approval without hanging).

Behavior parity notes
- Cadence follows rationale -> plan -> execution -> summary in behavior_goal.
- Review mode preserves Findings/Questions/Tests while still emitting command plans.
- Goal persistence binds follow-ups; `/goal clear` resets the active goal after completion.

Scope
- Interactive chat UX (prompt cadence, planning, approvals, footer)
- Task continuity and follow-up resolution
- TUI controls and discoverability
- Logging, privacy, and trust UX affordances
- UAT harness reliability (socket mode)

Manual UX checks (by ticket)

UX6: Fix duplicate footer render in chat flow
- Start `martin`, run a simple command plan.
- Expect: footer/status banner prints once per turn; no duplicate banner lines.

BX1: Task chain memory + follow-up resolver
- Set a goal: `/goal set audit ux parity`.
- Ask for a plan; reply "do it".
- Expect: follow-up resolves to active goal with no extra questions.

BX2: Behavior summaries per turn
- Run any command plan.
- Expect: a "Summary" line appears with action + next step.

BX3: Decision visibility + rationale snippets
- Trigger a command plan.
- Expect: "Rationale" line appears with brief reason.

BX4: Context continuity pack
- Run `/context` then execute a plan.
- Expect: context summary shows goal + next action and updates after completion.

BX5: Clarification gating rules
- Ask a clear question (no ambiguity).
- Expect: no clarifying questions; Martin proceeds with assumptions if needed.

BX7: Output cadence guardrails
- Trigger a command plan.
- Expect: cadence order is rationale -> plan -> execution -> results -> summary.
- Expect: a live "Working: thinking · request n/3" line appears during LLM response time.

BX8: Goal thread persistence
- Set a goal, close/reopen, say "continue".
- Expect: goal persists and follow-up binds to it.

CX7/CX16/CX39: Context harvesting + auto-surface
- Start a session.
- Expect: concise context summary and "since last session" delta appear.
- Expect: startup does not hang when launched outside a repo (fast context scan).

CX10/CX34/CX44/CX58: TUI input and help discoverability
- Enter TUI (`/tui` or configured entry).
- Expect: palette + key hints + help overlay; keybindings listed in `/keys`.

CX37/CX38: File picker + open-from-diff
- Use `/files` to insert a path.
- Trigger an edit diff; expect line anchors and open hints.

CX41: Review mode formatting parity
- Toggle `/review on`, ask for a review.
- Expect: Findings/Questions/Tests structure.

CX43/CX51: Tests UI + rerun
- Run `/tests`, then `/rerun test`.
- Expect: last test status is recorded; rerun uses policy checks.

CX45/CX46/CX47: Status banner + process panel + heartbeat
- Long-running command or simulated loop.
- Expect: banner updates; process panel shows worklog/heartbeat entries.

CX48: Clock-in/out UX
- Start session and exit with `quit`.
- Expect: clock-in/out entries are written without prompting for a note.

CX49/CX60: Operator MO + onboarding
- First-run or `/onboarding`.
- Expect: checklist mentions `/verify`, tickets, bug log, tests, and signoff.
- Expect: startup progress bar shows preflight/clock-in/context/onboarding steps.

BX11: Request audit logging
- Run a short behavior scenario.
- Expect: `request_audit` entries in `logs/researcher_ledger.ndjson` and `logs/martin.log`.

CX54: Privacy mode UX
- `/privacy on`, run a command, then `/privacy off`.
- Expect: no ledger/log writes while privacy is on; mode is acknowledged.

CX55: Binary/large file safety
- `/open` a large/binary file.
- Expect: safe stub/truncation warning, no crash.

CX56: Model/provider status UX
- `/status` and check banner.
- Expect: model name + local-only warning if applicable.

CX57: Workspace boundary guardrails
- Attempt a command with a path outside repo.
- Expect: explicit confirmation or hard block, with log entry.

UAT harness checks (socket)

UAT1: Normalize prompts for socket harness auto-wait
- Run harness with `use_socket: true`.
- Expect: auto-wait finds prompts reliably.

UAT3: De-duplicate outputs in socket harness mode
- Run harness with `--echo` and socket mode.
- Expect: output appears once (no double-printed lines).

UAT4: Socket handshake/timeout handling
- Run harness while CLI socket is unavailable.
- Expect: clear failure message and exit (no hang).

UAT5: Emit prompt-ready events over socket
- Harness waits only on explicit prompt events.
- Expect: inputs sent only after prompt event.

UAT7: Mailbox mode async logging
- Run harness with `--mailbox` or scenario `mailbox: true`.
- Expect: NDJSON events appended to `logs/uat_mailbox.ndjson` and session auto-exits after duration.

UAT8: Test socket parity
- Run harness with `use_socket: true` and confirm context summary appears.
- Expect: no behavior changes vs stdin mode beyond I/O transport.

DOC4: Expected behavior discoverability
- Run `/help` and check `docs/cli_ux.md`.
- Expect: both reference `docs/expected_behavior.md`.

Security UX checks

SEC1: Gate test socket inputs
- Attempt connecting from non-loopback or without token.
- Expect: connection rejected or input ignored.

Recordkeeping
- Log any failures in `docs/bug_log.md`.
- Create or update a ticket with acceptance criteria if a behavior is missing.
