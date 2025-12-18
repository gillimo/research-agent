Signoff (2025-12-18)
====================

Summary
- Docs refreshed (README, architecture, setup, tickets, work_plan, hall_of_fame, project_plan) to match shipped CLI and current state.
- CLI available: `python -m researcher status|ingest|ask|plan|nudge` (pipe-friendly `--stdin`, optional `--use-llm`); bridge remains at `scripts/researcher_bridge.py`.
- RAG stack: FAISS + `all-MiniLM-L6-v2` with SimpleIndex fallback; sample ingest/query working; sanitized Martin v1.4.7 artifact present.
- Tests: `python -m pytest` → 9 passed.
- UX doc: `docs/cli_ux.md` added with entry-point, flows, and output expectations.
- Cloud hop: `ask` supports `--cloud-mode always --cloud-cmd "$env:CLOUD_CMD"` with sanitized prompt, hashed logging to `logs/cloud/`, and provenance table for cloud outputs.

Next priorities
1) Cloud librarian bridge with allowlist/sanitization + logging to `logs/cloud/`.
2) Cloud hop integration into `ask` with provenance/confidence merge and optional snippet ingest.
3) Internal abilities surface + stronger supervisor loop to keep coding agents nudged.
4) Packaging/CI/docs polish after cloud path lands.

HOF note
- Cleared to work larger (5x) batches and take two tickets at a time without extra check-ins.
