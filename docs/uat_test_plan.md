# UAT Test Plan (CLI UX + Behavior)

Purpose
- Validate that the CLI feels like Codex (workflow, responsiveness, readability, continuity).
- Confirm safety, privacy, and trust workflows remain intact while UX is upgraded.

Scope
- Interactive chat UX (prompt/response layout, footer/heartbeat)
- Command planning, approvals, and execution
- Context continuity, tasks, and follow-ups
- Logging, privacy, and verification flows
- Remote workflows (pairing, tunnel, relay policy)

Pre-reqs
- `python` available on PATH
- Run `/verify` and resolve missing items
- Set `MARTIN_ENCRYPTION_KEY` if encryption is enabled

Automation harness (optional)
- Run scripted sessions: `python scripts/uat_harness.py --scenario scripts/uat_scenarios/basic.json --echo`
- Auto-wait sends inputs only when a prompt appears (disable with `--no-auto-wait`)
- Set `--prompt-timeout 0` to wait forever for a prompt
- Add `--transcript logs/uat_transcript.txt` to capture a session log
- Extend scenario JSON with `steps[].wait_for` to assert expected output
- Use `steps[].wait_for_event` to assert socket events (e.g., `prompt`, `input_used`)
- Use `event_log` (or `--event-log`) to capture all socket/stdout events in NDJSON for later review
- Socket mode sends inputs over a test socket (`use_socket: true` in scenario)
- Test socket can also be toggled via `MARTIN_TEST_SOCKET=1` (config: `test_socket`)
- Mailbox mode is async: fire inputs, log outputs to `logs/uat_mailbox.ndjson`, and check later.
- Use mailbox scenario: `python scripts/uat_harness.py --scenario scripts/uat_scenarios/mailbox.json --echo`

Test matrix (manual UAT)

1) Startup UX
- Steps: launch `martin`
- Expect: banner shows git, mode, model, host, goal/next action (if any)
- Expect: footer (if enabled) stays readable and separated from scrollback

2) Context continuity
- Steps: `/goal set build ux parity`, ask a question, then say "do it"
- Expect: follow-up resolves to active goal without asking extra questions
- Expect: `/goal status` shows current goal

3) Plan + approvals
- Steps: ask to list files in repo
- Expect: "Proposed command plan" with rationale, risk tags, approval prompt
- Run with `yes` and verify outputs

4) Edit/inline/editor flow
- Steps: trigger plan, choose `edit` or `inline`
- Expect: edited commands used and logged

5) Retry + fix loop
- Steps: run a failing command
- Expect: failure recorded, `/retry` re-runs, fix-loop prompts appear

6) Privacy mode
- Steps: `/privacy on`, run a command, `/privacy off`
- Expect: no ledger/tool logs recorded during privacy

7) Logs + outputs
- Steps: run a long output command
- Expect: output stored under `logs/outputs`, summarized in console

8) RAG ingest + trust labels
- Steps: `/ingest docs/starter_rag/00_readme.md`
- Expect: trust label `internal`, no blocked paths, ingestion success

9) Librarian note handling
- Steps: `/librarian inbox`
- Expect: trust score + stale flag, local-only blocks are explained

10) Verification + tests
- Steps: `/verify`, then `scripts/run_tests.ps1 -SkipInstall`
- Expect: clear next steps in `/verify`; tests run if deps installed

11) Remote pairing + tunnel
- Steps: `/host pair testbox`, `/remote config set ssh_host host`, `/remote status`
- Expect: validation reports missing config until set; status shows running/stopped

12) Remote relay policy
- Steps: set `execution.remote_policy=relay`, choose non-local host, run a harmless command
- Expect: command attempts remote execution (SSH) and logs remote_command

13) Export + encryption
- Steps: enable `trust_policy.encrypt_exports=true`, set key, `/export session logs/test.json`
- Expect: output is `.enc`, decrypt with `/decrypt`

Success criteria
- No crashes or hangs in any scenario
- Outputs are readable and footers do not obscure the scrollback
- Safety gates always appear where expected
- Behavior feels consistent with Codex (minimal questions, proactive execution)
