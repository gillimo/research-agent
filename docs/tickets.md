Ticket Backlog (priority, deps, status)
=======================================

Legend: [ ] todo, [~] in progress, [x] done

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

P3 – Martin UX parity (local-first)
- [~] M1: Port Martin behaviors to `researcher`  
  Command-plan extraction, smart runner (non-interactive), diagnosis loop, progress summaries, rephraser toggle; pipe-friendly.  
  Deps: C1, C2, F2.
- [ ] M2: Internal abilities  
  Implement `martin.<ability>` dispatch for env check, plan.extract_commands, dev.create_file (append-only), diagnose.  
  Deps: M1.
- [~] M3: Agent oversight/prompting for coding agents  
  Supervisor loop to monitor coding agents and keep prompting when idle/stopped (per docs), following guardrails; configurable prompts.  
  Deps: M1, C1.

P4 – Cloud librarian
- [ ] L1: Cloud bridge with guardrails  
  Provider wrapper; sanitized prompts; outbound allowlist; structured logging to `logs/cloud/`.  
  Deps: F2.
- [ ] L2: Cloud hop integration  
  Optional/heuristic cloud fetch; provenance tagging; optional ingest of cloud snippets into RAG.  
  Deps: L1, C2.

P5 – Quality/automation
- [ ] Q1: Auto-update triggers  
  Confidence/recall heuristic to re-query cloud or re-chunk/ingest.  
  Deps: C2, L2.
- [ ] Q2: Packaging/runtime setup  
  `requirements.txt`/`pyproject`, venv bootstrap scripts, optional Windows launcher.  
  Deps: C1.
- [ ] Q3: CI stub + test suite  
  Run pytest; lint/format hooks; GitHub Actions placeholder.  
  Deps: F3, C1–C2.
- [ ] Q4: Ingest tooling + sample data  
  Sample docs + scripted ingest demo; idempotent re-ingest.  
  Deps: C2.
- [ ] Q5: Performance + caching  
  Basic cache for repeated queries; timing stats in `status`; index warm/load toggle.  
  Deps: C2, L2.
- [ ] Q6: Error handling + UX polish  
  Exit codes, clearer messages, redaction of sensitive errors; non-zero on failure.  
  Deps: C1, M1.
- [ ] Q7: Documentation refresh  
  Update README/setup/examples to match shipped CLI and defaults.  
  Deps: C1–C3, M1–M2, L1–L2, Q1–Q6.

P6 – Legacy alignment
- [x] A1: Capture sanitized Martin v1.4.7 artifact  
  Stored at `martin_v1_4_7.py`; requires `OPENAI_API_KEY` from env.  
  Deps: S1, S2.
- [ ] A2: Import legacy requirements  
  Parse `AgentMartin_Operating_Manual.pdf` and `AgentMartin_Full_Ticket_Ledger.pdf`; add any missing behaviors to tickets.  
  Deps: none.
