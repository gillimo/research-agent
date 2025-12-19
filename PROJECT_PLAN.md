## Two-Agent System (Local Researcher + Cloud Assistant) — Project Plan

Goal
- Build a low-trust, proprietary-aware local agent ("researcher") that can answer/tool requests, backed by a cloud model for breadth. The local agent keeps a RAG store and only asks the cloud for generic information through sanitized queries.

Architecture (target)
- Local model/runtime: run the researcher locally (Ollama + phi3 mini by default; swap if hardware permits).
- RAG store: embeddings + vector DB + doc cache. Auto-update when gaps are detected.
- Agent protocol: local agent handles user/tool requests; if it needs external info, it formulates a sanitized query to the cloud agent; cloud answers feed back into local RAG.
- Tooling hook: expose a CLI/service interface so other tools (e.g., PoGo builder) can call the researcher behind a clean API/IPC.
- Guardrails: prompt sanitization, allowlist of outbound queries, logging of cloud interactions.
- UX parity: Martin-style command extraction/plan/diagnosis running locally with provenance-aware answers.

Current state (2025-12-18)
- CLI shipped: `ask`, `ingest`, `status`, `plan`, `nudge`, `abilities`, `supervise`; stdin-friendly and pipe-ready.
- RAG online: FAISS + `all-MiniLM-L6-v2` embedding by default with SimpleIndex fallback; sample ingest/query working; tests extended.
- Safety: secrets moved to `.env` (gitignored); sanitized Martin v1.4.7 artifact for reference.
- Observability: rotating local log at `logs/local.log`; cloud logs at `logs/cloud/cloud.ndjson`; provenance table in CLI outputs.
- Cloud path: Librarian IPC + cloud bridge integrated; optional ingest of cloud snippets.

MVP milestones
1) Choose local model + embedding stack and stand up a simple RAG store with ingest/retrieval. ✅
2) Define the request/response schema between local and cloud agents; add sanitization rules. ✅
3) Build a CLI harness to issue queries and show provenance (local vs cloud). ?
4) Add auto-RAG update triggers when confidence/recall is low. ?
5) Document deployment/run steps and logging expectations. ?
6) Add cloud librarian hop with sanitization + provenance and optional ingestion. ?

Open decisions
- Cloud provider/model and cost/latency constraints.
- Sanitization policy refinements and observability (what gets logged, redacted) for cloud hops.
- Auto-trigger thresholds for cloud queries and re-chunk/ingest.

Signed: Codex (2025-12-18)
