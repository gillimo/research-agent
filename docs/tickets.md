Ticket Backlog (priority, deps, status)
=======================================

Legend: [ ] todo, [~] in progress, [x] done

Next Priority Order
1) CL7: IPC timeout/cancel support
2) CL8: IPC error taxonomy
3) CL9: Sanitization assertions at boundaries
4) CL11: Structured IPC logging
5) CL12: Inbox retention policy


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

P7 ? Codex CLI parity
- [x] CX1: Approval policy modes (on-request/on-failure/never) and per-command escalation
- [x] CX2: Sandboxing modes (read-only/workspace-write/full) with enforcement
- [x] CX3: Command output summarization for long outputs
- [x] CX4: Plan tool with explicit plan state tracking
- [x] CX5: Test/run helpers with suggested next steps  
  Added `/tests` slash command with repo-aware suggestions; added unit tests for helper selection.
- [x] CX6: Diff/patch preview workflow for edits  
  Dev flow now shows unified diff preview and prompts for approval; env `MARTIN_AUTO_APPLY=1` or approval_policy=never auto-applies; added preview test.
- [x] CX7: Auto-context harvesting (git status, recent changes, repo summary)
- [x] CX8: Resource/tool registry with list/read APIs  
  Added `resources`/`resource` CLI + slash commands and internal abilities.
- [x] CX9: Review mode heuristics (bugs/risks/tests focus)  
  Heuristic review intent + review behavior guidance wired in orchestration/prompt.
- [x] CX10: Rich TUI input (autocomplete/history/slash suggestions)  
  Slash autocomplete + fuzzy slash matching + persistent readline history + input history search/clear + history pick + command palette with descriptions + palette pick + simple path completion + rich table panels (when available).
- [x] CX32: Context refresh shortcuts + context-on-start UX  
  `/context refresh` forces a new context pack; chat startup prints a 1-line context summary; prompt includes last command summary when available.  
  Deps: CX7.

P8 ? Codex parity audit (2025-12-29)
- [x] CX11: Session persistence + resume  
  Save/restore chat state (plan, last commands, approvals, context) across restarts; expose `/resume` and auto-resume on launch.  
  Acceptance: persists session state (plan, approvals, cwd, context refs) to disk; `/resume` restores and prints a brief summary; failures do not crash and are logged.  
  Deps: CX12.
- [x] CX12: Deterministic tool execution ledger  
  Record structured tool calls + outputs with timestamps, cwd, exit codes; surface in `/outputs` and allow export.  
  Acceptance: ledger entries capture command/tool, args, cwd, duration, exit code, output summary; secrets redacted; `/outputs` lists and can export JSON.  
  Deps: CX13.
- [x] CX13: Command safety classifier  
  Gate risky commands (rm, git reset, registry edits) with stronger confirmation; add allow/deny list by path and command.  
  Acceptance: classifier assigns risk level; high-risk requires explicit confirmation or is blocked by policy; allow/deny list lives in `config/local.yaml`; unit tests cover common risky commands.  
  Deps: none.
- [x] CX14: Interactive command editing  
  Allow user to edit a generated command before execution; support "explain" and "dry run".  
  Acceptance: pre-exec prompt supports edit/approve/reject; `--dry-run` prints without executing; `--explain` shows rationale.  
  Deps: CX13.
- [x] CX15: Inline file diff viewer  
  Side-by-side or unified diff with paging; link back to file/line for follow-up edits.  
  Acceptance: diff view supports paging; references file/line anchors for follow-up; binary files show a safe stub.  
  Deps: CX6.
- [x] CX16: Repo-aware context pack  
  Auto-attach repo map (top files, tech stack, recent changes, open PRs if available) and refresh on `/context`.  
  Acceptance: `/context` shows repo summary (tree, languages, recent changes, open PRs if any); context pack can be saved and reused; redaction rules apply.  
  Deps: CX12.
- [x] CX17: Task queue + reminders  
  Persist long-running tasks, show `next` action, and prompt on idle (beyond current supervisor loop).  
  Acceptance: tasks persist across sessions; `/tasks` lists with next action; idle reminders reference open tasks; supports mark done.  
  Deps: CX11.
- [x] CX18: Local-only mode hardening  
  Single toggle to disable all cloud calls and verify no outbound requests; warn if cloud config is present.  
  Acceptance: local-only mode blocks cloud calls and logs denials; startup warns if cloud env vars are set; tests confirm no network usage.  
  Deps: CX13.
- [x] CX19: Librarian health + IPC diagnostics  
  `/librarian status --verbose` with socket ping, last error, and cloud connectivity check.  
  Acceptance: verbose status reports socket ping, last error from ledger, and cloud connectivity; non-zero exit on failure.  
  Deps: CX12.
- [x] CX20: Resource redaction policy tests  
  Unit + integration tests for sanitization/allowlist, including edge cases (paths, emails, secrets).  
  Acceptance: tests cover file paths, emails, tokens, registry keys, and command snippets; CI runs tests without real keys.  
  Deps: CX13.

P9 ? Codex parity audit (2025-12-29, follow-ups)
- [x] CX21: Approval policy parity across flows  
  Apply approval_policy consistently in chat, plan, fix-commands, and internal abilities.  
  Acceptance: on-request requires explicit approval in every flow; on-failure auto-runs after a failure only; never auto-approves everywhere; tests cover each flow.  
  Deps: CX1.
- [x] CX22: Sandbox enforcement edge cases  
  Expand sandbox detection for PowerShell redirects, git write ops, and pathless writes; add tests.  
  Acceptance: blocks writes outside workspace reliably on Windows and POSIX; tests cover redirects, git, and pip installs.  
  Deps: CX2.
- [x] CX23: Command explainability  
  Show a short rationale alongside proposed commands and approvals.  
  Acceptance: every command plan includes a brief "why" line; override available to hide/show.  
  Deps: CX14.
- [x] CX24: Diff navigation helpers  
  Add file/line anchors and quick open hints for diffs shown in the UI.  
  Acceptance: diffs include file paths with line numbers when available; works for unified diffs.  
  Deps: CX15.
- [x] CX25: Tool ledger filtering + search  
  Filter ledger by time, cwd, risk, and rc; add a simple text search.  
  Acceptance: `/outputs ledger --filter rc!=0` and `--since 1h` behave; tests cover filters.  
  Deps: CX12.
- [x] CX26: Session export bundle  
  Export transcript + ledger + context pack into a single JSON/zip.  
  Acceptance: `/export session <path>` writes a bundle with consistent schema; no secrets in output.  
  Deps: CX11, CX12, CX16.
- [x] CX27: Context diff summaries  
  Show what changed since last session (git diff/stat + recent files delta).  
  Acceptance: `/context` includes a "since last session" block when snapshots exist.  
  Deps: CX11, CX16.
- [x] CX28: Local-only enforcement checks  
  Runtime guard that aborts cloud calls even if config/env is set.  
  Acceptance: cloud invocations are blocked and logged; tests verify no network on local-only mode.  
  Deps: CX18.
- [x] CX29: Librarian verbose status details  
  Report IPC ping latency, last error, and cloud credentials presence.  
  Acceptance: `/librarian status --verbose` includes latency and last error info with non-zero exit on failure.  
  Deps: CX19.
- [x] CX30: Review mode UX polish  
  Add explicit review mode toggle that forces bug/risk/test focus.  
  Acceptance: `/review on|off` changes response format and includes test guidance.  
  Deps: CX9.
- [x] CX31: Fix-command review parity  
  Provide edit/explain/dry-run options for fix commands in the diagnosis loop (chat + plan flows).  
  Acceptance: fix-command prompt supports edit/explain/dry-run and logs the selected action.  
  Deps: CX14.

P10 ? Librarian-driven RAG growth (local-first)
- [x] L3: Librarian request API from Martin  
  Add explicit commands for Martin to ask the Librarian for background research, gap filling, and RAG refresh.  
  Acceptance: `/librarian request <topic>` enqueues a sanitized cloud request and logs it; Martin receives status updates.  
  Deps: L2, CX12.
- [x] L4: Proactive RAG update loop  
  Librarian periodically proposes new ingest/update tasks based on low-confidence queries and topic gaps.  
  Acceptance: gap signals are logged; Martin sees a prompt with suggested updates; user can approve/deny.  
  Deps: L3, Q1.
- [x] L5: RAG update chat between Martin and Librarian  
  Introduce a lightweight “advice” channel where Librarian can message Martin with new sources, summaries, and update plans.  
  Acceptance: Martin receives periodic update notes; `/librarian inbox` lists pending notes; can accept to ingest.  
  Deps: L3, CX12.
- [x] L6: Cloud update ingest pipeline  
  On approval, Librarian can fetch/prepare sanitized sources and trigger ingestion into local RAG.  
  Acceptance: ingest reports provenance, logs cloud snippet hashes, and updates index without blocking chat.  
  Deps: L4, C2.

P11 ? Librarian sources discovery
- [x] L7: Source discovery requests  
  Allow Martin to request public source suggestions and ingest them as notes.  
  Acceptance: `/librarian sources <topic>` returns a note; `/librarian accept <n>` ingests sources text.  
  Deps: L3.

P12 ? Trust process audit follow-ups
- [x] CX33: Explicit sanitize before cloud hop  
  Ensure chat flow sanitizes prompts before any Librarian call; add assertion/guard in client.  
  Acceptance: cloud-bound prompts are sanitized in `cli.py` and verified in `librarian_client.py`.  
  Deps: L1, L2.
- [x] DOC1: Cloud log path consistency  
  Align docs to `logs/cloud/cloud.ndjson` and remove stale `.log` mentions.  
  Acceptance: README and UX docs reference the correct path.  
  Deps: none.
- [x] DOC2: Agent mode trust disclosure  
  Clarify `/agent on` auto-approves commands and how it interacts with approval policy.  
  Acceptance: AGENTS and architecture docs state the behavior explicitly.  
  Deps: CX1.
- [x] B1: Privacy no-log actually disables ledger/log sinks  
  Ensure session privacy mode suppresses `state_manager` ledger writes and `martin.log` writes.  
  Acceptance: no-log prevents ledger/log writes; tests cover; docs updated.  
  Deps: CX54.

P13 ? Codex UX parity (additional)
- [x] CX34: Full TUI shell  
  Multi-pane TUI with selectable lists, key-driven navigation, and status panels.  
  Acceptance: interactive panes for palette, tasks, logs, and context; keyboard navigation without slash commands.  
  Notes: selectable lists, detail panes, and key navigation added.  
  Deps: CX10.
- [x] CX35: Inline command editor UI  
  Provide a dedicated editing UI for proposed commands with preview and edit buffer.  
  Acceptance: user can open editor, modify, and approve without re-typing in prompt.  
  Notes: inline editor prompt added alongside external editor flow.  
  Deps: CX14.
- [x] CX36: Diff viewer everywhere  
  Ensure any edit path (not just dev_flow) shows unified diff preview with paging.  
  Acceptance: all file edits show diff preview and optional paging before apply.  
  Notes: diff previews now cover transcript/export/output logs and append-only ability writes.  
  Deps: CX6, CX15.
- [x] CX37: Interactive file picker  
  Add a file/browser picker (recent files, repo tree) to insert paths into commands.  
  Acceptance: palette or slash command opens a picker with filtering and selection.  
  Deps: CX10.
- [x] CX38: Quick-open from diffs  
  Provide interactive “open file at line” actions from diff output.  
  Acceptance: diff view offers selectable file/line targets.  
  Notes: diff previews emit /open hints and slash command displays snippets.  
  Deps: CX15, CX36.
- [x] CX39: Context pack auto-surface  
  Automatically present context changes since last session without manual `/context`.  
  Acceptance: on session start or before plan execution, display context delta.  
Notes: context updates auto-surface on session start and before plan runs.  
  Deps: CX16.
- [x] CX40: Task queue UX panel  
  Add a dedicated task view (list/add/complete) in TUI with reminders.  
  Acceptance: tasks visible/editable in TUI and via slash commands.  
  Notes: TUI task list supports add (a) and done (x).  
  Deps: CX17, CX34.
- [x] CX41: Review mode formatting parity  
  Structured review output with sections for bugs, risks, and tests similar to Codex.  
  Acceptance: review mode enforces structured output and includes test guidance block.  
Notes: review responses enforce Findings/Questions/Tests format.  
  Deps: CX9.
- [x] CX42: Palette search across files/tests/outputs  
  Extend palette to search files, recent outputs, and test commands.  
  Acceptance: palette query returns file paths, test shortcuts, and output logs.  
  Notes: palette now includes file/test/output matches when querying.  
  Deps: CX34.
- [x] CX43: Test run UI with last-run status  
  Track last test command, status, and duration; surface in TUI/palette.  
  Acceptance: `/tests` shows last-run status and rerun option.  
  Notes: /tests run executes and records last status; TUI shows last test in context.  
  Deps: CX5, CX34.
- [x] CX44: TUI theming/branding consistency  
  Standardize colors, headers, and panel layout to feel like Codex CLI.  
  Acceptance: a consistent theme applies across TUI panels.  
  Notes: consistent TUI theme applied to panels and headers.  
  Deps: CX34.
- [x] CX45: Workspace status banner  
  Show branch/dirty state, last command status, and active mode at top of TUI.  
  Acceptance: banner updates on changes and appears in chat.  
  Deps: CX16, CX34.
- [x] CX46: Active process chat panel  
  Add a dedicated panel for "process chat" updates (thinking/plan/doing/done/next) streamed during work.  
  Acceptance: panel is visible in TUI, updates in real time, and can be toggled.  
  Notes: TUI process panel shows worklog entries and can be toggled.  
  Deps: CX34, CX44.
- [x] CX47: Heartbeat/worklog stream  
  Emit periodic heartbeat summaries during long operations and store to a lightweight worklog.  
  Acceptance: heartbeat appears in process panel; a `last 10` view is available.  
  Notes: heartbeat emits to worklog and appears in process panel.  
  Deps: CX46.
- [x] CX48: Clock-in/out prompts in UI  
  Prompt for clock-in on session start and clock-out on exit, writing to `docs/logbook.md`.  
  Acceptance: prompts are visible in chat/TUI and can be skipped with a reason.  
  Notes: chat and TUI prompt for clock-in/out with skip reasons.  
  Deps: CX34.
- [x] CX49: Internalize Martin coding MO  
  Bake the operator guide rules into runtime checks and prompts (pre-flight git status, tickets, bug log, docs, tests, signoff).  
  Acceptance: startup and exit flows enforce/verify the MO; non-compliance is surfaced with next steps.  
  Notes: preflight checks and exit reminders enforce the MO.  
  Deps: DOC3, CX34.
- [x] CX50: Interrupt/cancel running commands  
  Provide a reliable way to stop long-running commands (Ctrl+C or UI action) with clear status updates.  
  Acceptance: cancel is logged, user sees a confirmation, and the agent resumes cleanly.  
Notes: Ctrl+C cancels running commands and logs cancellation.  
  Deps: CX34.
- [x] CX51: Rerun last command/test shortcut  
  Add a quick action to rerun the last command or last test with safety prompts.  
  Acceptance: slash command or palette action reruns last command/test with approval/sandbox checks.  
Notes: /rerun command|test replays last run with policy checks.  
  Deps: CX42, CX43.
- [x] CX52: Output search/filter UX  
  Add output search/filtering in TUI and palette (by command, rc, or text).  
  Acceptance: a user can filter recent outputs and open the matching log quickly.  
Notes: /outputs search and TUI filter provide output discovery.  
  Deps: CX25, CX34.
- [x] CX53: Installable martin launcher  
  Provide a system-wide launcher (Windows shim/PATH) and uninstall flow.  
  Acceptance: `martin` works from any shell; uninstall removes shim cleanly.  
Notes: install/uninstall scripts added for Windows PATH shim.  
  Deps: Q2.
- [x] CX54: Session privacy controls  
  Add session-level controls to redact/omit sensitive content in transcripts/log exports.  
  Acceptance: a `no-log` or redacted mode prevents sensitive output from being persisted.  
Notes: /privacy no-log mode skips transcript and ledger persistence.  
  Deps: CX12, CX26.
- [x] CX55: Binary/large file safety  
  Handle binary or large files safely in /open and diff previews with size caps.  
  Acceptance: binary files show safe stubs and large files are truncated with warnings.  
Notes: binary/large file previews are skipped with warnings.  
  Deps: CX15, CX38.
- [x] CX56: Model/provider status UX  
  Surface local/cloud model status in banner and /status with warnings.  
  Acceptance: banner shows current model/provider and local-only warnings.  
Notes: status banner includes model info and warnings.  
  Deps: CX18.
- [x] CX57: Workspace boundary guardrails  
  Prompt/log when operating outside repo root and require confirmation.  
  Acceptance: explicit confirmation for commands outside workspace with log entry.  
Notes: outside-workspace commands require confirmation and are logged.  
  Deps: CX13.
- [x] CX58: Keybindings/help discoverability  
  Add a `/keys` view or TUI help overlay showing active bindings.  
  Acceptance: keybinding help is available in chat and TUI.  
Notes: /keys command documents chat/TUI keybindings.  
  Deps: CX34.
- [x] CX59: Recovery and retry UX  
  Offer a resume/redo prompt after crashes or failed commands.  
  Acceptance: last failed command can be retried safely with approval.  
Notes: last failed command prompts /retry and is tracked in state.  
  Deps: CX11, CX12.
- [x] CX60: First-run onboarding wizard  
  Add a guided setup flow (local-only toggle, handle, tests, log paths).  
  Acceptance: first run steps are shown once and can be re-run via command.  
Notes: onboarding wizard runs on first launch and via /onboarding.  
  Deps: DOC3, CX49.

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
- [ ] CL7: IPC timeout/cancel support  
  Allow Martin to cancel long-running Librarian tasks.  
  Acceptance: cancel message stops work and logs outcome.  
  Deps: CL2.
- [ ] CL8: IPC error taxonomy  
  Define structured error codes (timeout, sanitize_block, invalid_payload, etc.).  
  Acceptance: errors include code + message; tests verify.  
  Deps: CL1.
- [ ] CL9: Sanitization assertions at boundaries  
  Enforce redaction flags and verify sanitized prompts before egress.  
  Acceptance: both client and server assert `sanitized=true` on cloud calls.  
  Deps: CX33.
- [ ] CL10: Ingest allowlist validation  
  Validate ingest paths/text sources against allowlist rules.  
  Acceptance: invalid paths are rejected and logged.  
  Deps: C2.
- [ ] CL11: Structured IPC logging  
  Log IPC request/response summaries with redaction hashes.  
  Acceptance: logs include request_id, sizes, and duration.  
  Deps: CL2.
- [ ] CL12: Inbox retention policy  
  Add retention and truncation policy for Librarian inbox items.  
  Acceptance: inbox capped and old entries archived.  
  Deps: L5.

- [ ] CL13: Librarian trust scoring  
  Tag librarian outputs with a trust score and provenance summary.  
  Acceptance: responses include trust score and source list in logs.  
  Deps: CL11.
- [ ] CL14: RAG source expiry + refresh  
  Track source freshness and prompt for refresh when stale.  
  Acceptance: stale sources flagged and suggested for refresh.  
  Deps: L4, C2.
- [ ] CL15: Sanitized query templates  
  Standard templates for librarian prompts with blocklists per domain.  
  Acceptance: librarian requests use templates and enforce blocklists.  
  Deps: CX33.
- [x] CL16: Passive upkeep cursor/index (gap events)  
  Avoid scanning the ledger file on every heartbeat; track last cursor or maintain a compact gap index.  
  Acceptance: upkeep reads incremental gap events with stable performance on large ledgers.  
  Deps: CL11.
- [ ] CL17: Passive gap note dedupe + rate limits  
  Deduplicate similar gap prompts and cap suggestions per topic per time window.  
  Acceptance: repeated gaps do not spam inbox; logs show dedupe decisions.  
  Deps: CL16, CL12.
- [ ] CL18: Adaptive heartbeat payload  
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
- [ ] G3: Proprietary data scanner for ingest  
  Scan for secrets/PII in ingested docs; warn or block.  
  Acceptance: scanner logs and respects allow/deny rules.  
  Deps: F2.
- [ ] G4: Host bootstrap script  
  One-command install to set up Martin on a new machine (venv + deps + config).  
  Acceptance: bootstrap script works on Windows and documents steps.  
  Deps: Q2.
- [ ] G5: Background service/daemon  
  Optional service mode to run Martin/Librarian persistently.  
  Acceptance: service starts/stops and logs status.  
  Deps: L1.
- [ ] G6: Remote session handoff  
  Export/import session context to move Martin between machines.  
  Acceptance: `/export session` can be imported safely on another host.  
  Deps: CX26.
- [ ] G7: Trust policy config  
  Central policy file for what can leave the machine and under what conditions.  
  Acceptance: policy enforced by sanitizer + IPC + cloud bridge.  
  Deps: CX33.
- [ ] G8: Redaction audit report  
  Generate a report of redaction decisions over time.  
  Acceptance: CLI can export a redaction audit summary.  
  Deps: CX12, CX20.
- [x] G9: Seed RAG starter pack for Martin  
  Provide starter documents that teach how Martin operates, how to be an agent, and safe workflows.  
  Acceptance: initial docs live in `docs/starter_rag` and are referenced in setup docs; `ingest` can load them.  
  Notes: starter RAG docs added under `docs/starter_rag`.  
  Deps: C2, DOC3.

- [ ] G10: Remote pairing + device registry  
  Register machines and pair Martin to hosts with explicit approval.  
  Acceptance: device list stored locally; pairing requires user confirmation.  
  Deps: L1, CX13.
- [ ] G11: Secure remote transport  
  Encrypted channel for remote control (mTLS or SSH tunnel).  
  Acceptance: remote commands travel over authenticated encrypted transport.  
  Deps: G10.
- [ ] G12: Remote command relay policy  
  Enforce the same sandbox/approval policies on remote hosts.  
  Acceptance: policy violations block remote execution with clear logs.  
  Deps: G11, CX13.
- [ ] G13: Multi-host UX  
  Allow switching target host and show active host in the banner.  
  Acceptance: `/host list|use <id>` works and banner shows host.  
  Deps: G10.
- [ ] G14: Remote data protection  
  Encrypt at-rest logs and caches on remote hosts and sync redaction rules.  
  Acceptance: remote logs are encrypted and follow trust policy.  
  Deps: G11, G7.
- [ ] G15: RAG trust labeling  
  Label RAG sources by trust level and enforce retrieval constraints.  
  Acceptance: sources marked public/internal and filtered by policy.  
  Deps: G7, C2.


P16 ? Operator guidance
- [x] DOC3: Martin operator guide  
  Provide a single authoritative Markdown guide for Martin's operating rules and workflows.  
  Acceptance: `docs/martin_operator_guide.md` covers workflow, safety, cloud rules, and logging.  
  Deps: AGENTS.md.

P17 ? File governance parity
- [ ] FG1: Workspace write hard-block option  
  Add a policy toggle to refuse all writes outside repo root even with approval.  
  Acceptance: when enabled, out-of-repo writes are blocked with a clear error and log entry.  
  Deps: CX57.
- [ ] FG2: External editor provenance hook  
  Capture pre/post hash for edited files when external editor flow is used.  
  Acceptance: ledger records file path + hash delta for editor-applied changes.  
  Deps: CX12.
- [ ] FG3: Centralized file-write policy gate for IPC ingest  
  Enforce a single write policy for Librarian ingest paths and any IPC-triggered writes.  
  Acceptance: ingest rejected if path violates policy; logged with request_id.  
  Deps: CL2, CL10.
