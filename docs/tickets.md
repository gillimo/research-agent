Ticket Backlog (priority, deps, status)
=======================================

Legend: [ ] todo, [~] in progress, [x] done

Next Priority Order
1) CX10: Rich TUI input (autocomplete/history/slash suggestions)
2) CX5: Test/run helpers with suggested next steps
3) CX6: Diff/patch preview workflow for edits
4) DOC1: Cloud log path consistency
5) DOC2: Agent mode trust disclosure


P0 – Safety/Secrets
- [x] S1: Secret hygiene for legacy artifacts  
  Remove embedded keys; enforce env-only keys; rescan repo.  
  Deps: none.
- [x] S2: Git ignores for secrets/state  
  `.env`, `.martin_state.json`, logs/data caches ignored; `.env.example` added.  
  Deps: none.

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
