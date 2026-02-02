Ticket Backlog (priority, deps, status)
=======================================

Legend: [ ] todo, [~] in progress, [x] done
Tag legend: [OPENCODE] [SOCKET] [SOCKETBRIDGE] [LOCAL] [BUG] [RAG] [TEST] [DOCS]

Next Priority Order
1) G4V: Verify host bootstrap script on clean machine
2) G5V: Verify service script on clean machine


P0 – Safety/Secrets
- [x] S1: Secret hygiene for legacy artifacts  
  Remove embedded keys; enforce env-only keys; rescan repo.  
  Deps: none.
- [x] S2: Git ignores for secrets/state  
  `.env`, `.martin_state.json`, logs/data caches ignored; `.env.example` added.  
  Deps: none.
- [x] S3: Harden cloud cmd_template execution  
  Avoid `shell=True`, escape prompts, and prevent logging raw templates or secrets.  
  Acceptance: cmd_template uses safe argv; sanitized prompts only; template not logged verbatim.  
  Deps: L1.
- [x] S4: Expand redaction patterns + pre-log scrubbing  
  Add coverage for common tokens (JWT, bearer), Linux paths, URLs, env var patterns; scrub log_event payloads.  
  Acceptance: sanitizer covers common secret classes; logs/ledger show redacted values; tests added.  
  Deps: F2.

P1 – Foundations (models, schemas, tests)
- [x] F1: Embedding/vector choice + config  
  Defaults set to public `all-MiniLM-L6-v2` + FAISS in `config/local.yaml` with SimpleIndex fallback and mock index path separation.  
  Deps: none.
- [x] F2: Request/response schema + sanitization rules  
  Captured in `docs/architecture.md`; includes allowlist/redaction guidance.  
  Deps: none.
- [x] F3: Test harness + fixtures  
  Pytest suite (9 passing): sanitization/allowlist, schema validation, FAISS ingest/retrieve, CLI args/stdin.  
  Deps: F1, F2.

P2 – Core local loop
- [x] C1: `researcher` CLI  
  Commands: `ask`, `ingest`, `status`, `plan`, `nudge`; stdin piping; provenance/confidence in output; local LLM optional.  
  Deps: F1, F2.
- [x] C2: Ingestion + RAG plumbing  
  Loader + chunker; embed/index writer; retrieval with scored chunks and sources; FAISS default with SimpleIndex fallback.  
  Deps: F1.
- [x] C3: Observability (local)  
  Standard log format, provenance tags, rotation/size limits; logs at `logs/local.log`.  
  Deps: C1, C2.

P3 ??" Martin UX parity (local-first)
- [x] M1: Port Martin behaviors to `researcher`  
  Command-plan extraction, smart runner (non-interactive), diagnosis loop, progress summaries, rephraser toggle; pipe-friendly.  
  Deps: C1, C2, F2.
- [x] M2: Internal abilities  
  Implement `martin.<ability>` dispatch for env check, plan.extract_commands, dev.create_file (append-only), diagnose.  
  Deps: M1.
- [x] M3: Agent oversight/prompting for coding agents  
  Supervisor loop to monitor coding agents and keep prompting when idle/stopped (per docs), following guardrails; configurable prompts.  
  Deps: M1, C1.
- [x] M4: System context provider (programmatic)  
  Collect safe system info (OS, paths, user dirs, drives) via a dedicated ability/IPC for portability.  
  Deps: M1.
- [x] M5: Agent protocol file (`AGENTS.md`)  
  Define Martin's operating rules and usage so tools can run him consistently.  
  Deps: M1.
P4 – Cloud librarian
- [x] L1: Cloud bridge with guardrails  
  Provider wrapper; sanitized prompts; outbound allowlist; structured logging to `logs/cloud/`.  
  Deps: F2.
- [x] L2: Cloud hop integration  
  Optional/heuristic cloud fetch; provenance tagging; optional ingest of cloud snippets into RAG.  
  Deps: L1, C2.

P5 – Quality/automation
- [x] Q1: Auto-update triggers  
  Confidence/recall heuristic to re-query cloud or re-chunk/ingest.  
  Deps: C2, L2.
- [x] Q2: Packaging/runtime setup  
  `requirements.txt`/`pyproject`, venv bootstrap scripts, optional Windows launcher.  
  Deps: C1.
- [x] Q3: CI stub + test suite  
  Run pytest; lint/format hooks; GitHub Actions placeholder.  
  Deps: F3, C1–C2.
- [x] Q4: Ingest tooling + sample data  
  Sample docs + scripted ingest demo; idempotent re-ingest.  
  Deps: C2.
- [x] Q5: Performance + caching  
  Basic cache for repeated queries; timing stats in `status`; index warm/load toggle.  
  Deps: C2, L2.
- [x] Q6: Error handling + UX polish  
  Exit codes, clearer messages, redaction of sensitive errors; non-zero on failure.  
  Deps: C1, M1.
- [x] Q7: Documentation refresh  
  Update README/setup/examples to match shipped CLI and defaults.  
  Deps: C1–C3, M1–M2, L1–L2, Q1–Q6.

P6 – Legacy alignment
- [x] A1: Capture sanitized Martin v1.4.7 artifact  
  Stored at `martin_v1_4_7.py`; requires `OPENAI_API_KEY` from env.  
  Deps: S1, S2.
- [x] A2: Import legacy requirements  
  Parse `AgentMartin_Operating_Manual.pdf` and `AgentMartin_Full_Ticket_Ledger.pdf`; add any missing behaviors to tickets.  
  Deps: none.

P7 ? OpenCode CLI replacement
- [x] [OPENCODE] OC0: Local clone of OpenCode
  Clone upstream repo into local workspace for integration.
  Acceptance: repo exists at `projects/opencode`.
  Deps: none.
- [ ] [OPENCODE] OC1: Fork or mirror OpenCode repo under gillimo
  Decide fork vs vendor copy; keep upstream remote; document update flow.
  Deps: OC0.
- [x] [OPENCODE][LOCAL] OC2: Local model wiring for OpenCode
  Configure LOCAL_ENDPOINT + default local model; verify Ollama discovery.
  Acceptance: OpenCode starts in repo and uses local model by default.
  Deps: OC0.
- [x] [OPENCODE][DOCS] OC3: Guardrails context injection
  Add opencode.local.md with Martin guardrails; ensure OpenCode loads it.
  Acceptance: contextPaths include opencode.local.md and AGENTS.md.
  Deps: OC2.
- [ ] [OPENCODE] OC4: MCP bridge to Researcher
  Expose researcher CLI as MCP tool for ask/ingest/status/librarian.
  Acceptance: OpenCode can call Researcher via MCP with approval prompts.
  Deps: OC2.
- [ ] [OPENCODE][DOCS] OC5: CLI replacement plan + cutover
  Map old CLI commands to OpenCode flows; define transition checklist.
  Acceptance: documented migration steps and rollback path.
  Deps: OC2, OC3.
- [ ] [OPENCODE] OC6: Build + install automation (Windows)
  Provide build script and local install wrapper for OpenCode.
  Acceptance: one command builds opencode.exe and launches from repo.
  Deps: OC1.
- [ ] [OPENCODE][TEST] OC7: Migrate tests/validation to OpenCode
  Port or re-run existing CLI verification steps for OpenCode-based flow.
  Acceptance: updated test plan + pass/fail checklist for OpenCode CLI.
  Deps: OC4, OC5.
- [~] [SOCKET][SOCKETBRIDGE] OC8: Migrate socket tooling to SocketBridge
  Wire SocketBridge as the supported local transport for agent I/O.
  Acceptance: OpenCode can communicate via SocketBridge with auth.
  Deps: OC4.
- [~] [SOCKET][SOCKETBRIDGE][TEST] OC9: Validate SocketBridge integration
  Investigate end-to-end socket flow with OpenCode + Researcher.
  Acceptance: reproducible test steps with logs and expected outputs.
  Deps: OC8.
- [ ] [BUG][LOCAL] OC10: Local-only mode without Librarian
  Fix bug so local model can run with Librarian disabled/offline.
  Acceptance: OpenCode + Researcher works in local-only without cloud or Librarian.
  Deps: OC2.
- [ ] [RAG] OC11: RAG ingestion repair
  Fix ingestion path so local RAG updates correctly in the new flow.
  Acceptance: ingest updates index and retrieval shows new sources.
  Deps: OC4.

P14 ? Martin-Librarian communication gaps
- [x] CL1: IPC protocol versioning + schema validation  
  Add protocol version and strict schema validation on both ends.  
  Acceptance: invalid or unknown versions are rejected with clear errors.  
Notes: protocol_version enforced on Librarian IPC requests/responses.  
  Deps: L1.
- [x] CL2: Request/response correlation IDs  
  Ensure every IPC request includes a stable `request_id` and responses echo it.  
  Acceptance: logs and ledger entries show request_id end-to-end.  
  Deps: CL1.
- [x] CL3: Message size limits + chunking  
  Enforce max payload sizes and chunk large requests (e.g., ingest text).  
  Acceptance: oversized payloads fail gracefully; chunking reassembles.  
  Deps: CL1.
- [x] CL4: Local auth/allowlist for IPC  
  Restrict IPC to local user context (token or filesystem-based secret).  
  Acceptance: unauthorized clients are rejected; tests cover denial.  
  Deps: CL1.
- [x] CL5: Heartbeat + health metrics  
  Add heartbeat messages and expose last-seen timestamps.  
  Acceptance: `/librarian status --verbose` shows last heartbeat age.  
  Deps: CX19.
- [x] CL6: Retry/backoff + circuit breaker  
  Standardize retry policy with circuit breaker on repeated failures.  
  Acceptance: backoff logged; breaker prevents spam.  
  Deps: L1.
- [x] CL7: IPC timeout/cancel support  
  Allow Martin to cancel long-running Librarian tasks.  
  Acceptance: cancel message stops work and logs outcome.  
  Deps: CL2.
- [x] CL8: IPC error taxonomy  
  Define structured error codes (timeout, sanitize_block, invalid_payload, etc.).  
  Acceptance: errors include code + message; tests verify.  
  Deps: CL1.
- [x] CL9: Sanitization assertions at boundaries  
  Enforce redaction flags and verify sanitized prompts before egress.  
  Acceptance: both client and server assert `sanitized=true` on cloud calls.  
  Deps: CX33.
- [x] CL10: Ingest allowlist validation  
  Validate ingest paths/text sources against allowlist rules.  
  Acceptance: invalid paths are rejected and logged.  
  Deps: C2.
- [x] CL11: Structured IPC logging  
  Log IPC request/response summaries with redaction hashes.  
  Acceptance: logs include request_id, sizes, and duration.  
  Deps: CL2.
- [x] CL12: Inbox retention policy  
  Add retention and truncation policy for Librarian inbox items.  
  Acceptance: inbox capped and old entries archived.  
  Deps: L5.

- [x] CL13: Librarian trust scoring  
  Tag librarian outputs with a trust score and provenance summary.  
  Acceptance: responses include trust score and source list in logs.  
  Deps: CL11.
- [x] CL14: RAG source expiry + refresh  
  Track source freshness and prompt for refresh when stale.  
  Acceptance: stale sources flagged and suggested for refresh.  
  Deps: L4, C2.
- [x] CL15: Sanitized query templates  
  Standard templates for librarian prompts with blocklists per domain.  
  Acceptance: librarian requests use templates and enforce blocklists.  
  Deps: CX33.
- [x] CL16: Passive upkeep cursor/index (gap events)  
  Avoid scanning the ledger file on every heartbeat; track last cursor or maintain a compact gap index.  
  Acceptance: upkeep reads incremental gap events with stable performance on large ledgers.  
  Deps: CL11.
- [x] CL17: Passive gap note dedupe + rate limits  
  Deduplicate similar gap prompts and cap suggestions per topic per time window.  
  Acceptance: repeated gaps do not spam inbox; logs show dedupe decisions.  
  Deps: CL16, CL12.
- [x] CL18: Adaptive heartbeat payload  
  Emit heartbeat only on changes or include concise health metrics (last request, queue length, last error).  
  Acceptance: heartbeat is less noisy and includes health summary fields.  
  Deps: CL5.


P15 ? Project goal completion (local control + proprietary safety)
- [x] G1: Local-only default posture  
  Default to local-only unless explicitly enabled by the user.  
  Acceptance: config defaults to local-only; clear warning when enabling cloud.  
  Deps: CX18.
- [x] G2: Cloud prompt preview + approve  
  Show sanitized cloud prompt and require approval before sending.  
  Acceptance: user can approve/deny per cloud call.  
  Deps: CX14, CX33.
- [x] G3: Proprietary data scanner for ingest  
  Scan for secrets/PII in ingested docs; warn or block.  
  Acceptance: scanner logs and respects allow/deny rules.  
  Deps: F2.
- [x] G4: Host bootstrap script  
  One-command install to set up Martin on a new machine (venv + deps + config).  
  Acceptance: bootstrap script works on Windows and documents steps.  
  Deps: Q2.
- [x] G5: Background service/daemon  
  Optional service mode to run Martin/Librarian persistently.  
  Acceptance: service starts/stops and logs status.  
  Deps: L1.
- [x] G6: Remote session handoff  
  Export/import session context to move Martin between machines.  
  Acceptance: `/export session` can be imported safely on another host.  
  Deps: CX26.
- [x] G7: Trust policy config  
  Central policy file for what can leave the machine and under what conditions.  
  Acceptance: policy enforced by sanitizer + IPC + cloud bridge.  
  Deps: CX33.
- [x] G8: Redaction audit report  
  Generate a report of redaction decisions over time.  
  Acceptance: CLI can export a redaction audit summary.  
  Deps: CX12, CX20.
- [x] G9: Seed RAG starter pack for Martin  
  Provide starter documents that teach how Martin operates, how to be an agent, and safe workflows.  
  Acceptance: initial docs live in `docs/starter_rag` and are referenced in setup docs; `ingest` can load them.  
  Notes: starter RAG docs added under `docs/starter_rag`.  
  Deps: C2, DOC3.

- [x] G10: Remote pairing + device registry  
  Register machines and pair Martin to hosts with explicit approval.  
  Acceptance: device list stored locally; pairing requires user confirmation.  
  Deps: L1, CX13.
- [x] G11: Secure remote transport  
  Encrypted channel for remote control (mTLS or SSH tunnel).  
  Acceptance: remote commands travel over authenticated encrypted transport.  
  Deps: G10.
- [x] G16: Remote tunnel CLI helper  
  Provide `/remote start|stop|status` to manage the transport process.  
  Acceptance: tunnel status can be queried and PID tracked locally.
- [x] G12: Remote command relay policy  
  Enforce the same sandbox/approval policies on remote hosts.  
  Acceptance: policy violations block remote execution with clear logs.  
  Deps: G11, CX13.
- [x] G13: Multi-host UX  
  Allow switching target host and show active host in the banner.  
  Acceptance: `/host list|use <id>` works and banner shows host.  
  Deps: G10.
- [x] G14: Remote data protection  
  Encrypt at-rest logs and caches on remote hosts and sync redaction rules.  
  Acceptance: remote logs are encrypted and follow trust policy.  
  Deps: G11, G7.
- [x] G17: Encrypted export bundles  
  Encrypt session/tool ledger exports when remote or policy requires.  
  Acceptance: exports are written as `.enc` when encryption is enabled.
- [x] G15: RAG trust labeling  
  Label RAG sources by trust level and enforce retrieval constraints.  
  Acceptance: sources marked public/internal and filtered by policy.  
  Deps: G7, C2.

- [ ] G4V: Verify host bootstrap script on clean machine  
  Run `scripts/install_martin.ps1` on a clean machine and confirm venv + deps + shim.  
  Acceptance: install succeeds and `martin` launches without manual steps.  
  Notes: local run timed out during pip install; venv created. Blocked on clean machine. Use `docs/clean_machine_run_log.md`.  
  Deps: G4.
- [ ] G5V: Verify service script on clean machine  
  Validate `scripts/martin_service.ps1 start|stop|status` on a clean machine.  
  Acceptance: service starts/stops and PID tracking works.  
  Notes: local run timed out while running `scripts/run_tests.ps1`; clean-machine verification pending. Use `docs/clean_machine_run_log.md`.  
  Deps: G5.
- [x] G23: Add verification checklist command  
  Add `/verify` to print a checklist for bootstrap/service/pytest and venv status.  
  Acceptance: command reports missing items and suggests next steps.
- [x] G24: Add onboarding verification doc  
  Document clean-machine verification steps for install/service/tests.  
  Acceptance: doc covers bootstrap, service, and test commands.
- [x] G25: Add test runner script for venv setup  
  Add `scripts/run_tests.ps1` to ensure venv + pytest and run tests.  
  Acceptance: tests run from a clean machine after script execution.
- [x] G26: Add verification checklist to onboarding  
  Include `/verify` in the onboarding checklist.  
  Acceptance: onboarding prompt references `/verify`.
- [x] G27: Add verification docs to CLI UX  
  Mention `/verify` and `docs/verification.md` in CLI UX docs.  
  Acceptance: CLI UX lists verification resources.
- [x] G28: Add install/test skip flags  
  Add `-SkipDeps` to install and `-SkipInstall` to test runner for slow environments.  
  Acceptance: scripts support flags without breaking defaults.
- [x] G29: Improve /verify guidance  
  Include `next_steps` suggestions in `/verify` output.  
  Acceptance: `/verify` reports actionable next steps.
- [x] G30: Update verification doc with timeouts  
  Document slow install/test guidance and skip flags.  
  Acceptance: `docs/verification.md` mentions timeouts and flags.
- [x] G18: Remote command execution over transport  
  Implement command relay to remote host once transport is active.  
  Acceptance: commands route to selected host with same policy checks.  
  Deps: G11, G12.
- [x] G21: Key management helpers  
  Add `/trust keygen` and file encrypt/decrypt helpers.  
  Acceptance: keygen prints a new key; encrypt/decrypt work with env key.  
  Deps: G14.
- [x] G22: Key rotation helper  
  Add `/rotate <path> <old_env> <new_env>` to re-encrypt bundles.  
  Acceptance: rotated bundles are written to a new file.  
  Deps: G20.
- [x] G19: Remote transport credentials storage/validation  
  Validate and persist remote transport config for paired hosts.  
  Acceptance: invalid configs are blocked; status reports missing fields.  
  Deps: G11.
- [x] G20: Trust policy key management + rotation  
  Provide guidance and helper for rotating encryption keys safely.  
  Acceptance: docs + CLI helper update key and re-encrypt exports.  
  Deps: G14.


P16 ? Operator guidance
- [x] DOC3: Martin operator guide  
  Provide a single authoritative Markdown guide for Martin's operating rules and workflows.  
  Acceptance: `docs/martin_operator_guide.md` covers workflow, safety, cloud rules, and logging.  
  Deps: AGENTS.md.

P17 ? File governance parity
- [x] FG1: Workspace write hard-block option  
  Add a policy toggle to refuse all writes outside repo root even with approval.  
  Acceptance: when enabled, out-of-repo writes are blocked with a clear error and log entry.  
  Deps: CX57.
- [x] FG2: External editor provenance hook  
  Capture pre/post hash for edited files when external editor flow is used.  
  Acceptance: ledger records file path + hash delta for editor-applied changes.  
  Deps: CX12.
- [x] FG3: Centralized file-write policy gate for IPC ingest  
  Enforce a single write policy for Librarian ingest paths and any IPC-triggered writes.  
  Acceptance: ingest rejected if path violates policy; logged with request_id.  
  Deps: CL2, CL10.

P18 ? Codex behavior parity (conversation + model feel)
- [x] BX1: Task chain memory + follow-up resolver  
  Persist an active goal and last actions; resolve short follow-ups ("do that", "continue") to the active chain.  
  Acceptance: follow-ups map to the latest task/plan; banner shows active goal + next action.
- [x] BX2: Behavior summaries per turn  
  Emit a 1-2 line "what I did / what's next" summary after tool runs (toggleable).  
  Acceptance: summary appears after command plans; can be disabled in config.
- [x] BX3: Decision visibility + rationale snippets  
  Persist and display brief rationale alongside proposed commands and approvals.  
  Acceptance: ledger and UI show a short rationale block for plan proposals.
- [x] BX4: Context continuity pack  
  Add a compact active context block (goal, tasks, last plan, last result) used to steer behavior.  
  Acceptance: `/context` and startup banner include this block; it updates per turn.
- [x] BX5: Clarification gating rules  
  Reduce over-questioning: only ask when blocked; otherwise proceed and state assumptions.  
  Acceptance: behavior aligns with Codex-style minimal questions; assumptions are explicit.
- [x] BX6: Model support parity (local)  
  Add local model selection/health in `/status`, optional streaming, and fallback model policy.  
  Acceptance: operator can switch local model; streaming toggle works; fallback is logged.
- [x] BX7: Output cadence guardrails  
  Normalize response cadence (progress note -> actions -> results) to match Codex CLI feel.  
  Acceptance: outputs follow a consistent flow; tests validate output formatting.
- [x] BX8: Goal thread persistence  
  Maintain an active goal until explicitly cleared; follow-ups bind to it.  
  Acceptance: `/goal` shows/sets/clears; short follow-ups resolve to active goal.
- [x] BX9: Follow-up resolver respects slash commands + review mode  
  Skip follow-up resolution for slash commands and review-mode prompts; tighten short-followup detection to avoid overriding intent.  
  Acceptance: `/review on` does not update the active goal; short prompts like "please review this repo" are not rewritten as follow-ups.
- [x] BX10: Review mode preserves structured output even with command plans  
  Ensure review-mode responses still include Findings/Questions/Tests even when `command:` lines are present, or defer structured output until after plan execution.  
  Acceptance: review-mode requests show the structured headings in the assistant response without breaking command extraction.
- [x] BX11: Request handling audit in logs  
  Emit per-request audit events with intent, outcome, clarifying-question count, and satisfaction status to mirror Codex-style behavior tracking.  
  Acceptance: each user turn logs a request_audit entry with intent, action_taken, result, and followup_needed=false/true.
- [x] BX12: Review mode defaults to current workspace  
  Avoid asking for repo URL/path when running inside a repo; use current workspace context by default.  
  Acceptance: "please review this repo" proceeds without asking for the path.

P19 ? UX behavior inventory
- [x] UX1: Operator-visible behavior inventory  
  Produce a canonical list of UX behaviors/abilities and keep it updated.  
  Acceptance: `docs/cli_ux.md` or new doc enumerates behaviors and shortcuts.
- [x] UX2: Live update + process stacking expectations  
  Document how Martin stacks processes (plans, retries, background checks) and updates live.  
  Acceptance: doc covers plan execution, retries, and streaming behavior.
- [x] UX3: Tooling capability catalog  
  Document internal abilities, command flows, and audit/ledger coverage.  
  Acceptance: doc lists abilities and how to invoke them.
- [x] UX4: Test/verify workflow expectations  
  Document `/verify`, test runner, and how to update docs/logs during changes.  
  Acceptance: doc defines the workflow and acceptance steps.
- [x] UX5: Surface behavior inventory from /help  
  Add a pointer to `docs/ux_behaviors.md` in `/help` output.  
  Acceptance: `/help` prints the doc reference.

P20 ? UAT harness stability
- [x] UAT1: Normalize prompts for socket harness auto-wait  
  Strip ANSI or emit explicit prompt tokens when using test socket mode.  
  Acceptance: auto-wait detects prompts reliably in socket mode with ANSI prompts.
- [x] UAT2: Unit test prompt normalization  
  Add a lightweight test for ANSI prompt matching in the harness.  
  Acceptance: test covers ANSI prompt string and passes.
- [x] UAT3: De-duplicate outputs in socket harness mode  
  Avoid double-printing stdout when socket streaming is active.  
  Acceptance: output appears once in harness echo and buffer.
- [x] UAT4: Socket handshake/timeout handling  
  Fail fast or fall back if the harness cannot connect to the test socket.  
  Acceptance: harness reports a clear error instead of hanging.
- [x] UAT5: Emit prompt-ready events over socket  
  Add explicit "prompt" events for the harness to detect readiness without ANSI parsing.  
  Acceptance: harness can auto-wait on prompt events only.
- [x] UAT6: Fix socket input consumption in test bridge  
  Ensure `input` messages sent over the test socket are queued and consumed by CLI prompts.  
  Acceptance: harness receives input_ack and CLI progresses past prompts.
- [x] UAT8: Test socket mode parity check  
  Add a lightweight test or harness check that context auto-surface runs in test socket mode.  
  Acceptance: test socket runs show context summary without special-casing test mode.
- [x] UAT9: Event-based waits for socket harness  
  Add `wait_for_event` support so scenarios can wait for prompt/input events instead of text.  
  Acceptance: harness can wait for `prompt` or `input_used` events in socket mode.
- [x] UAT10: Update socket scenarios to wait for prompt events  
  Add `wait_for_event` for `loop_ready` and `prompt` before sending inputs.  
  Acceptance: scenarios no longer race and are stable in socket mode.
- [x] UAT11: Always-available event log  
  Add `event_log` to capture socket/stdout events in NDJSON for any run.  
  Acceptance: `event_log` records events outside mailbox mode.
- [x] UAT12: Default event log in scenarios  
  Add `event_log` to socket scenarios so runs always emit NDJSON traces.  
  Acceptance: basic and mailbox scenarios write `logs/uat_events.ndjson`.
- [x] UAT13: Snapshot capture for UX review  
  Add `screenshot_dir` to write per-step output snapshots (tail) for quick UX review.  
  Acceptance: scenarios emit `logs/uat_snapshots/step_*.txt`.
- [x] UAT14: Loop readiness event reliability  
  Ensure `loop_ready` is emitted before steps begin in socket UAT.  
  Acceptance: harness receives `loop_ready` without timeouts.
- [x] UAT15: Context summary gating  
  Ensure `martin: Context:` appears before first input in socket UAT.  
  Acceptance: scenarios can wait for the context summary without timing out.
- [x] UAT16: Clear socket read timeout  
  Disable the 1s socket timeout after connect to avoid dropping event streams.  
  Acceptance: socket output events continue beyond the initial ping/pong.
- [x] UAT17: Slow-start socket timeouts  
  Increase socket scenario `wait_for_event` timeouts to reduce flakiness.  
  Acceptance: loop_ready/prompt waits no longer time out on slow starts.
- [x] UAT18: Persistent socket input send  
  Send inputs over the persistent socket connection to ensure input_ack/input_used events are observed.  
  Acceptance: socket runs receive input_ack/input_used for each input.
- [x] UAT19: Input wait uses event buffer  
  Wait for `input_used` via event-buffer scan instead of threading.Event to avoid missed signals.  
  Acceptance: socket runs no longer warn about input consumption when events are present.
- [x] UAT20: Suppress redundant loop_ready warning  
  Skip the initial loop_ready warning when the scenario already waits for `loop_ready`.  
  Acceptance: socket runs avoid duplicate warnings when scenarios include the wait.
- [x] UAT21: Scenario approval ordering  
  Ensure socket scenarios respond to approval prompts before sending follow-up questions.  
  Acceptance: "Approve running" is answered before the next user input is sent.
- [x] UAT22: Input wait fallback  
  Accept matching `input_ack`/`input_used` events when the wait misses the exact signal.  
  Acceptance: socket runs avoid false "input not consumed" warnings when events are present.
- [x] UAT23: Wait for plan completion before follow-up  
  Ensure socket scenarios wait for plan completion text before sending the next question.  
  Acceptance: follow-ups are sent after "Done. OK" appears.
- [x] UAT24: Handle outside-workspace prompt in scenarios  
  Respond to the "Command touches outside workspace" confirmation before waiting for completion.  
  Acceptance: scenario does not hang at the outside-workspace prompt.
- [x] UAT25: Handle follow-up approval prompt  
  After the follow-up question, respond to the approval prompt before quitting.  
  Acceptance: scenario does not leave the plan approval prompt pending.
- [x] UAT26: Extend follow-up wait timeouts  
  Increase follow-up prompt/approval waits to handle slow prompt generation.  
  Acceptance: follow-up waits do not time out on slower runs.
- [x] UAT27: Conditional input support  
  Allow steps to send inputs only when specific prompt text or events appear.  
  Acceptance: scenarios can skip approval responses when prompts do not appear.
- [x] UAT28: De-duplicate socket event log  
  Avoid logging both raw and cleaned socket output in `event_log`.  
  Acceptance: each socket output appears once in `logs/uat_events.ndjson`.
- [x] UAT29: Gate approval responses in mailbox mode  
  Add `input_when_text` on approval prompts so mailbox runs don’t send early answers.  
  Acceptance: approval responses only send after the prompt appears.
- [x] UAT30: Deferred inputs for mailbox mode  
  Queue conditional inputs and send them once `input_when_text/event` becomes true.  
  Acceptance: mailbox runs can answer prompts asynchronously without skipping inputs.
- [x] UAT31: Extend mailbox duration  
  Increase mailbox scenario duration to allow prompts and deferred inputs to fire.  
  Acceptance: mailbox runs have enough time for approvals to appear.
- [x] UAT74: Mailbox quit should end runs even during active plans  
  Ensure mailbox auto-quit shuts down the CLI even if a plan is executing or awaiting approval.  
  Acceptance: mailbox runs exit within the configured duration without hanging.
- [x] UAT78: Mailbox exit prompt should not override approvals  
  Avoid sending an extra approval response during mailbox shutdown if the scenario already answered the prompt.  
  Acceptance: mailbox exit does not send duplicate "no" responses after a "yes" approval.
- [x] UAT75: Split behavior UAT into focused scenarios  
  Add smoke/review/goal scenarios with shorter durations for quicker UX checks.  
  Acceptance: each scenario completes in under 60 seconds and logs NDJSON events.
- [x] UAT76: Fast smoke profile for behavior UAT  
  Provide a short mailbox duration scenario for quick regressions.  
  Acceptance: smoke run finishes in under 45 seconds and captures prompt/response flow.
- [x] UAT77: Document mailbox check loop in UAT plan  
  Update the UAT plan to require checking mailbox logs after each scenario run.  
  Acceptance: `docs/uat_test_plan.md` includes the loop (run -> check mailbox -> rerun if needed).
- [x] UAT32: Gate mailbox follow-up on completion  
  Send the follow-up question only after "Done. OK" appears, and extend mailbox duration.  
  Acceptance: mailbox follow-up no longer lands mid-plan.
- [x] UAT33: Respect scenario mailbox_duration  
  Allow mailbox scenarios to override the default mailbox duration.  
  Acceptance: mailbox runs honor `mailbox_duration` in scenario JSON.
- [x] UAT34: Scope conditional inputs to new output  
  Track output/event cursors for pending inputs so conditions match only new output.  
  Acceptance: deferred inputs do not trigger on stale output.
- [x] UAT35: Defer conditional inputs in mailbox mode  
  Always queue conditional inputs in mailbox mode to avoid immediate sends.  
  Acceptance: mailbox mode no longer sends conditional inputs before prompts appear.
- [x] UAT36: Prompt-aware conditional checks  
  Treat matching prompt event text as satisfying `input_when_text`.  
  Acceptance: conditional inputs fire when their prompt appears, not on stale output.
- [x] UAT37: Consume conditional matches  
  Track baseline and consumed counts for prompt tokens so repeated prompts can be handled in order.  
  Acceptance: multiple `input_when_text` entries for the same token no longer fire on the first prompt.
- [x] UAT38: Avoid double-counting prompt tokens  
  Prefer prompt event text over output buffer when counting `input_when_text` occurrences.  
  Acceptance: duplicate conditional inputs stop firing on a single prompt.
- [x] UAT39: Fallback for non-prompt tokens  
  When prompt texts don’t include a token, count matches in output buffer.  
  Acceptance: conditional inputs for "Done. OK" can trigger in mailbox mode.
- [x] UAT40: Report pending inputs in mailbox mode  
  Emit a `pending_inputs` entry in the event log before quitting.  
  Acceptance: mailbox runs show pending input conditions in `logs/uat_events.ndjson`.
- [x] UAT41: Consume immediate conditional sends  
  Increment prompt/event counters when conditional inputs send immediately.  
  Acceptance: later conditional inputs do not fire on stale prompts.
- [x] UAT42: Log pending send actions  
  Emit a `pending_send` entry when deferred inputs are sent.  
  Acceptance: event log shows which conditional inputs fired.
- [x] UAT43: Non-mailbox conditional input readiness  
  Check latest prompt/output immediately for `input_when_text/event` so pre-existing prompts don’t queue forever.  
  Acceptance: conditional inputs fire immediately when prompt already visible outside mailbox mode.
- [x] UAT44: Normalize prompt events  
  Strip ANSI and de-dup near-identical prompt events in the test socket bridge.  
  Acceptance: prompt events are emitted once per prompt and use normalized text.
- [x] UAT45: Gate user inputs on user prompt  
  Update UAT scenarios to send user inputs only when the "You:" prompt appears.  
  Acceptance: user inputs are not injected during approval prompts.
- [x] UAT46: Prompt-only conditional inputs  
  Add `input_when_prompt` to gate steps on prompt text without falling back to output buffers.  
  Acceptance: prompt-gated inputs wait for prompt events and don’t fire on stale output.
- [x] UAT47: Scenario approval path alignment  
  Accept the plan approval in scenarios that expect the outside-workspace prompt.  
  Acceptance: scenario no longer waits on a prompt that can’t appear.
- [x] UAT48: Remove nondeterministic outside-workspace waits  
  Drop the outside-workspace prompt dependency from default UAT scenarios.  
  Acceptance: default scenarios run without requiring policy prompts.
- [x] UAT49: Conditional responses for policy/diagnosis prompts  
  Add prompt-gated "no" responses for outside-workspace and fix-command prompts.  
  Acceptance: scenarios don’t hang when policy or diagnosis prompts appear.
- [x] UAT50: Extend scenario wait timeouts  
  Increase Done OK/prompt wait times to tolerate slow diagnosis runs.  
  Acceptance: default scenarios no longer time out during long plans.
- [x] UAT51: Flush pending inputs during waits  
  Ensure conditional inputs are checked while waiting for prompts/events/text.  
  Acceptance: prompt-gated inputs fire without waiting for the next step boundary.
- [x] UAT52: Reorder scenarios around auto-start plans  
  Complete initial plan approvals before sending follow-up user questions.  
  Acceptance: user questions are not injected during auto-start approval prompts.
- [x] UAT53: Mailbox prompt gating uses latest prompt  
  Prevent prompt-gated inputs from firing unless the most recent prompt matches.  
  Acceptance: mailbox inputs do not reply to unrelated prompts.
- [x] UAT54: Gate mailbox questions after completion  
  Require Done OK + You prompt before sending mailbox questions.  
  Acceptance: mailbox questions no longer answer approval prompts.
- [x] UAT55: Trim mailbox scenario scope  
  Limit mailbox run to a single follow-up question to avoid pending input backlog.  
  Acceptance: mailbox scenario exits without pending inputs.
- [x] UAT56: Extend mailbox duration  
  Increase mailbox runtime to allow post-question approval prompts to be handled.  
  Acceptance: mailbox scenario no longer quits mid-approval.
- [x] UAT57: Prompt-text waits  
  Allow steps to wait for specific prompt text using prompt events.  
  Acceptance: scenarios can wait on "You:" or approval prompts without stdout scraping.
- [x] UAT58: Update scenarios to prompt waits  
  Replace stdout-based prompt waits with `wait_for_prompt` in default UAT scenarios.  
  Acceptance: scenarios rely on prompt events for readiness checks.
- [x] UAT59: Advance prompt cursor on initial approval  
  Use prompt-event waits for the first approval so later waits don’t match stale prompts.  
  Acceptance: follow-up questions wait for the correct approval prompt.
- [x] UAT60: Unit test prompt-text waits  
  Add coverage for `_wait_for_prompt_text` in the harness tests.  
  Acceptance: test passes with ANSI-colored prompt events.
- [x] UAT61: Prompt-text cursor coverage  
  Add a unit test to verify `_wait_for_prompt_text` advances the cursor between prompts.  
  Acceptance: sequential prompt waits succeed using the updated cursor.
- [x] UAT62: Prompt-only event filtering test  
  Add a unit test to ensure `_wait_for_prompt_text` ignores non-prompt events.  
  Acceptance: prompt waits do not match stdout events.
- [x] UAT63: Prompt-text timeout coverage  
  Add a unit test that exercises the timeout path when a prompt token is missing.  
  Acceptance: prompt waits return false on timeout.
- [x] UAT64: Prompt-text multi-token coverage  
  Add a unit test to verify `_wait_for_prompt_text` matches any token in a list.  
  Acceptance: prompt waits succeed when any token matches.
- [x] UX8: Heartbeat panel placement  
  Render a heartbeat/worklog summary below the main TUI panels.  
  Acceptance: heartbeat entries appear beneath main content in TUI.
- [x] UX9: Task chaining expectations  
  Document task queueing, chaining, and stacked process expectations in behavior docs.  
  Acceptance: expected behavior and UX inventory mention queueing/chaining explicitly.
- [x] UAT65: Behavior scenario prompt-first start  
  Remove loop_ready waits in behavior socket scenario to avoid hangs on resumed sessions.  
  Acceptance: behavior scenario waits on prompt events and starts reliably.
- [x] UAT66: Behavior scenario mailbox mode  
  Run behavior testing in mailbox mode to avoid auto-start approvals blocking prompt waits.  
  Acceptance: behavior scenario completes without prompt wait timeouts.
- [x] UAT67: Isolated state for behavior UAT  
  Allow the state file path to be overridden for test runs.  
  Acceptance: behavior UAT uses a dedicated state file without leaking active goals.
- [x] UAT68: Handle logbook prompts in behavior UAT  
  Add conditional responses for logbook handle and clock-in prompts in behavior scenario.  
  Acceptance: behavior scenario proceeds without manual logbook input.
- [x] UAT69: Logbook prompt socket parity  
  Use the shared read_user_input for the logbook handle prompt so test sockets receive it.  
  Acceptance: logbook prompt emits prompt events and accepts socket inputs.
- [x] UAT70: Mailbox prompt-only immediate send  
  Allow mailbox prompt-only inputs to send immediately when the current prompt matches.  
  Acceptance: prompt-only inputs respond to already-visible prompts.
- [x] UAT71: Flush pending on prompt events  
  Trigger pending input checks whenever a prompt event arrives in mailbox mode.  
  Acceptance: prompt-triggered inputs fire without waiting for the next mailbox tick.
- [x] UAT72: Bypass prompt counts on current prompt  
  If the latest prompt matches a pending input, allow it to send even when the prompt was already visible.  
  Acceptance: mailbox prompt inputs respond to existing prompts.
- [x] UAT73: Handle onboarding prompt in behavior UAT  
  Add a conditional response for the onboarding completion prompt.  
  Acceptance: behavior scenario proceeds without manual onboarding input.
- [ ] BX9: Review mode request respects review formatting  
  When `/review on` is active, a review request should return Findings/Questions/Tests even if a goal exists.  
  Acceptance: review prompt yields structured review output without goal continuation prompts.
- [ ] BX10: /goal clear clears active goal in agent mode  
  Ensure `/goal clear` resets the active goal and removes it from the banner.  
  Acceptance: goal line is empty after `/goal clear` in agent mode.
- [x] UX7: Codex parity behavior section  
  Document Codex-style behavior expectations in `docs/expected_behavior.md`.  
  Acceptance: expected behavior doc lists Codex parity behaviors and UX inventory references it.

P20a ? UAT harness stability (task breakdown)
- [x] UAT6a: Ensure socket inputs are accepted  
  Verify token matching and queue insert; emit input_ack on accept.  
  Acceptance: input_ack arrives for each sent input.
- [x] UAT6b: Confirm CLI consumes socket inputs  
  After input_ack, CLI prompt advances without manual stdin.  
  Acceptance: CLI prints next prompt or response after socket input.
- [x] UAT6c: Harness waits on input_ack  
  Use input_used as readiness signal before sending next input.  
  Acceptance: no dropped inputs in socket mode.
- [x] UAT5a: Prompt-ready on connect  
  Send last prompt to new socket client immediately.  
  Acceptance: harness sees prompt without waiting for new prompt.
- [x] UAT5b: Prompt-ready on each prompt  
  Emit prompt event every time `input()` is called.  
  Acceptance: harness auto-wait triggers reliably.
- [x] UAT1a: ANSI normalization in harness buffer  
  Strip ANSI before regex prompt detection.  
  Acceptance: auto-wait detects colored prompts.
- [x] UAT3a: Output de-dup in harness  
  Avoid double-reading stdout when socket output stream is active.  
  Acceptance: output lines appear once.
- [x] UAT4a: Socket connect/ping failure handling  
  Exit with clear error when socket not reachable.  
  Acceptance: no hang; error printed.

P20b ? UAT mailbox mode
- [x] UAT7a: Mailbox NDJSON logging  
  Log socket/stdout events to `logs/uat_mailbox.ndjson` in mailbox mode.  
  Acceptance: NDJSON entries appear with type/text/timestamp.
- [x] UAT7b: Mailbox keep-alive window  
  Keep session alive for `mailbox_duration` before exit.  
  Acceptance: events continue to stream during window.

P20c ? Mailbox robustness (extended behavior testing)
- [x] MB1: Long-lived mailbox session mode  
  Add a harness mode that keeps the CLI session open and allows multiple send/check cycles without auto-quit.  
  Acceptance: mailbox session stays alive across multiple input batches until explicitly closed.
- [x] MB2: Mailbox transcript aggregation  
  Aggregate socket/stdout events into a single response blob on demand (with cursor-based incremental fetch).  
  Acceptance: `collect()` returns only new output since last check.
- [x] MB3: Mailbox queue input API  
  Allow queued inputs to be sent asynchronously without blocking the harness thread.  
  Acceptance: inputs can be queued while the CLI is busy; they are sent once a prompt appears.
- [x] MB4: Prompt-aware async send  
  Gate queued inputs on prompt events (per prompt type) and log when they fire.  
  Acceptance: queued inputs only fire on matching prompt tokens.
- [x] MB5: Late-output capture window  
  Keep collecting output after the last input for a configurable grace period.  
  Acceptance: late responses are included in the next `collect()` call.
- [x] MB6: Mailbox session metadata  
  Record session id, start/end timestamps, and last prompt in the mailbox log.  
  Acceptance: NDJSON includes session metadata entries for each run.
- [x] MB7: Complex-task mailbox scenario  
  Add a scenario that sends a multi-step request, waits, then checks the mailbox for queue, plan, execution, and summary.  
  Acceptance: scenario asserts queue created, progress reported, summary present.
- [x] MB8: Mailbox regression checklist  
  Document the mailbox loop (send -> wait -> collect -> decide) and required checks in `docs/uat_test_plan.md`.  
  Acceptance: mailbox checklist includes late-output verification and prompt gating.

P20d ? Mailbox ambition follow-ups
- [x] MB9: Fix gpt-5 Responses payload/output parsing  
  Ensure Responses API calls yield assistant output for planner + main responses.  
  Acceptance: mailbox complex scenario logs a response and queue is created.
- [x] MB10: Mailbox complex scenario assertions  
  Require Action queue + progress + summary tokens in collect output.  
  Acceptance: complex mailbox scenario passes without missing token warnings.
- [x] MB11: Mailbox collect export file  
  Add `mailbox_collect_path` to write the latest collected output to a file for easy review.  
  Acceptance: collect step writes a TXT file with new output only.
- [x] MB12: Long-run transcript replay harness  
  Replay a multi-turn transcript via mailbox and assert invariants (plan->execute->summary).  
  Acceptance: replay run completes with all required tokens present.
- [x] MB13: Mailbox idle check-ins  
  Add optional periodic “heartbeat collect” for long runs.  
  Acceptance: event log shows periodic collect entries during idle waits.
- [x] MB14: Interactive socket console for UAT  
  Provide a CLI tool to send inputs to the test socket and stream outputs in real time for manual UAT.  
  Acceptance: console connects, prints outputs, and sends inputs without blocking.

P21 ? UX polish fixes
- [x] UX6: Fix duplicate footer render in chat flow  
  Remove redundant footer rendering after responses.  
  Acceptance: status footer renders once per turn.

P22 ? Security hardening (test tooling)
- [x] SEC1: Gate test socket inputs  
  Restrict test socket inputs to loopback-only or require a token.  
  Acceptance: non-local clients are rejected; local tests still work.

P23 ? Documentation coverage
- [x] DOC4: Link expected behavior doc from UX/help  
  Add `docs/expected_behavior.md` to CLI UX docs and `/help`.  
  Acceptance: `/help` and `docs/cli_ux.md` mention the expected behavior doc.
- [x] DOC5: UX test audit plan  
  Provide a test plan mapping UX tickets to manual checks.  
  Acceptance: `docs/ux_test_audit.md` exists and covers UX tickets.
