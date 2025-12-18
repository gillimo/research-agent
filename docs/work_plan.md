Work Plan (immediate)
=====================

Current focus (sequence)
1) L1: Cloud librarian bridge with sanitization + allowlist; add cloud logging to `logs/cloud/` and env-driven model/provider settings.
2) L2: Cloud hop integration into `ask` (merge provenance/confidence; optional ingest of snippets) with toggle/heuristic trigger.
3) M2: Internal abilities surface (env.check, diagnose, plan.extract_commands, dev.create_file append-only) and stronger supervisor loop to keep coding agents on-task.
4) M3: Agent oversight/keep-alive prompts tuned for coding agents; configurable prompt sets and idle thresholds.
5) Q7: Documentation refresh + demos once cloud path lands.

Test strategy (applied)
- Pytest fixtures for sample docs and fake env vars; mock cloud calls for offline runs.
- Validate request/response schemas and allowlist/redaction functions.
- Offline ingest/retrieve smoke: sample doc -> index -> top-k with sources.
- CLI argv/stdin coverage for `ask`, `ingest`, `plan`, and `nudge` (without real model execution).

Env/secrets
- Require `OPENAI_API_KEY` in `.env` (gitignored) for Martin artifact or cloud paths; no keys in repo.
- Default models in `config/local.yaml`; override via env as needed; embeddings default to `all-MiniLM-L6-v2` with FAISS + SimpleIndex fallback.

Notes
- Martin v1.4.7 artifact lives at repo root for reference/UX parity.
- Bridge (`scripts/researcher_bridge.py`) remains for pipe-friendly local/cloud demo; core CLI is already available.
