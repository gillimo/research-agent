Architecture
============

Overview
- Two agents: a local "researcher" that owns the RAG stack, and a cloud assistant used only via sanitized queries for breadth/recall. Local always mediates requests and writes back to its store.

Data flow (happy path)
1) User/tool calls the local CLI/service with a request.
2) Local retrieves from RAG (embeddings + vector store + doc cache) and drafts an answer.
3) If confidence/recall is low, the local agent emits a sanitized query to the cloud assistant (no proprietary data).
4) Cloud reply is logged, redacted if needed, and merged into the local RAG for provenance-aware responses.
5) Response is returned with provenance tags (local vs cloud).

Components
- Local runtime: Ollama (phi3 mini/128k by default; can swap if hardware allows).
- Embeddings + vector store: chosen defaults: `bge-small-en-v1.5` + FAISS index at `data/index/faiss.index` (configured in `config/local.yaml`) for low VRAM and offline-friendly indexing.
- Document cache: raw docs under `data/raw/`, chunked/cleaned under `data/processed/`.
- Cloud bridge: thin client to a selected provider/model; enforced allowlist of query patterns; logs to `logs/cloud/`.
- CLI/service: `scripts/researcher_bridge.py` today (pipe-friendly), with a future `researcher` CLI exposing `ask`, `ingest`, and `status`. Should be stdin-friendly for quick shell integration.
- Legacy artifact: sanitized Martin v1.4.7 snapshot at `martin_v1_4_7.py` for reference/porting of UX behaviors.
- Guardrails: prompt sanitization, PII/proprietary redaction rules, outbound allowlist, structured logging for every cloud call, provenance tags on answers.

Storage/layout (proposed)
- `data/raw/`           incoming documents
- `data/processed/`     cleaned/chunked docs
- `data/index/`         vector DB / embeddings
- `logs/`               local + cloud call logs
- `config/`             model + provider config (no secrets committed)
- `.env`                local secrets (ignored); `.env.example` shows required vars

Request/response schema (initial)
- Request: `{ "id": "<uuid>", "query": "<user text>", "mode": "ask|ingest", "context": { "tags": [], "files": [] }, "settings": { "k": 5, "cloud_allowed": false } }`
- Response: `{ "id": "<uuid>", "answer": "<text>", "provenance": { "local": [...], "cloud": [...] }, "confidence": { "local": 0.0, "cloud": 0.0 }, "logs_ref": "<path>" }`
- Ingest result: `{ "id": "<uuid>", "ingested": [{"path": "...", "chunks": n}], "errors": [] }`

Sanitization/guardrails (initial)
- Strip/replace obvious secrets (keys, tokens, emails, file paths) before cloud calls.
- Outbound allowlist: only generic questions; block commands, file paths, and user identifiers.
- Log every cloud call with hashes of prompt/response (not raw content) plus timestamps.

Test strategy (early)
- Unit tests: sanitization/allowlist, request/response schema validation, FAISS index writer/reader, CLI args/stdin handling.
- Integration smoke: ingest sample docs -> query returns top-k with sources (offline); bridge CLI runs local phi3 with stubbed cloud command.
- Deterministic fixtures: ship small sample docs for ingest tests; mock cloud calls for offline runs.
- Env safety: require `OPENAI_API_KEY` via `.env`/env vars; ensure tests do not read real keys (use fakes/mocks).

CLI integration
- Current bridge: `echo "question" | python scripts/researcher_bridge.py --stdin [--cloud-mode always --cloud-cmd "...{prompt}..."]`.
- Researcher CLI (skeleton): `python -m researcher status|ingest|ask --stdin/-k` with logging to `logs/local.log` and provenance table output.
- Return answers with a compact provenance block: local hits + cloud snippets.
