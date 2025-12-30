Work Plan (immediate)
=====================

Current focus (sequence)
1) Maintain Codex parity features and address any regressions.
2) Expand Librarian-driven RAG updates (sources discovery + approval pipeline).
3) Improve task queue automation and reminders.

Next 5 tickets (priority order)
1) CX10: Rich TUI input (finish richer panels)
2) CX32: Context refresh shortcuts + context-on-start UX (verify + docs)
3) CX31: Fix-command review parity (verify + tests)
4) CX5: Test/run helpers (audit coverage + polish)
5) CX6: Diff/patch preview workflow (audit coverage + polish)

Test strategy (applied)
- Pytest fixtures for sample docs and fake env vars; mock cloud calls for offline runs.
- Validate request/response schemas and allowlist/redaction functions.
- Offline ingest/retrieve smoke: sample doc -> index -> top-k with sources.
- CLI argv/stdin coverage for `ask`, `ingest`, `plan`, and `nudge` (without real model execution).
- Add unit coverage for command safety classifier and sandbox enforcement.
- Add ledger export tests and session resume tests (no secrets in snapshots).

Env/secrets
- Require `OPENAI_API_KEY` in `.env` (gitignored) for Martin artifact or cloud paths; no keys in repo.
- Default models in `config/local.yaml`; override via env as needed; embeddings default to `all-MiniLM-L6-v2` with FAISS + SimpleIndex fallback.

Notes
- Martin v1.4.7 artifact lives at repo root for reference/UX parity.
- Bridge (`scripts/researcher_bridge.py`) remains for pipe-friendly local/cloud demo; core CLI is already available.
