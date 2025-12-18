Work Plan (immediate)
=====================

Current focus (sequence)
1) F3: Test harness + fixtures (pytest skeleton; sanitization/schema/FAISS/CLI stdin tests). ✅  
2) C1: `researcher` CLI skeleton (ask/ingest/status; stdin; provenance). 🚧 (local-only; no LLM answer yet)  
3) C2: Ingestion + RAG plumbing (chunker, embeddings + FAISS/mock write/read, retrieval with sources). 🚧 (FAISS default + fallback)  
4) C3: Observability baseline (local logs with rotation; provenance tags). 🚧 (rotation/logs in place)  
5) M1/M3: Martin behaviors/oversight. 🚧 (plan/extract/run + nudge stub)

Test strategy (applied early)
- Use pytest with fixtures for sample docs and fake env vars; mock cloud calls.  
- Validate request/response schemas and allowlist/redaction functions.  
- Offline ingest/retrieve smoke: sample doc -> index -> top-k with sources.  
- CLI argv/stdin coverage for `ask` and `ingest` (without real model execution).

Env/secrets
- Require `OPENAI_API_KEY` in `.env` (gitignored) for Martin artifact or cloud paths; no keys in repo.  
- Default models in `config/local.yaml`; override via env as needed.

Notes
- Martin v1.4.7 artifact lives at repo root for reference/UX parity.  
- Bridge (`scripts/researcher_bridge.py`) remains for pipe-friendly local/cloud demo until full CLI lands.
