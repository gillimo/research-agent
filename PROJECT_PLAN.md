## Two-Agent System (Local Researcher + Cloud Assistant) — Project Plan

Goal
- Build a low-trust, proprietary-aware local agent ("researcher") that can answer/tool requests, backed by a cloud model for breadth. The local agent keeps a RAG store and only asks the cloud for generic information through sanitized queries.

Architecture (target)
- Local model/runtime: run the researcher locally (Ollama + phi3 mini by default; swap if hardware permits).
- RAG store: embeddings + vector DB + doc cache. Auto-update when gaps are detected.
- Agent protocol: local agent handles user/tool requests; if it needs external info, it formulates a sanitized query to the cloud agent; cloud answers feed back into local RAG.
- Tooling hook: expose a CLI/service interface so other tools (e.g., PoGo builder) can call the researcher behind a clean API/IPC.
- Guardrails: prompt sanitization, allowlist of outbound queries, logging of cloud interactions.

MVP milestones
1) Choose local model + embedding stack and stand up a simple RAG store with ingest/retrieval.
2) Define the request/response schema between local and cloud agents; add sanitization rules.
3) Build a CLI harness to issue queries and show provenance (local vs cloud).
4) Add auto-RAG update triggers when confidence/recall is low.
5) Document deployment/run steps and logging expectations.

Open decisions
- Local model choice (size, hardware fit), embedding model, vector store.
- Cloud provider/model and cost/latency constraints.
- Sanitization policy and observability (what gets logged, redacted).

Signed: Codex (2025-12-18)
