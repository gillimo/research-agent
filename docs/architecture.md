Architecture
============

Overview
- Two primary agents:
    - A **local "Researcher" agent** that owns the RAG stack/CLI, handles user requests, orchestrates tasks, and interacts with local LLMs. It directly mediates user requests and generates answers.
    - A **background "Librarian" process** responsible for managing cloud interactions (sanitized queries for breadth/recall), performing RAG upkeep (e.g., re-ingestion, re-chunking based on heuristics), and potentially creating/maintaining new RAGs. The Researcher agent communicates with the Librarian via Inter-Process Communication (IPC).

Data flow (happy path - with Librarian)
1) User/tool calls the local Researcher CLI/service with a request.
2) Researcher retrieves from its local RAG (embeddings + vector store + doc cache) and drafts an answer; optional local LLM generation (Ollama) can compose a response from retrieved context.
3) If confidence/recall is low (or explicitly requested by the user/Researcher logic), the Researcher sends a sanitized query request to the background Librarian via IPC.
4) The Librarian processes the request, formulates a highly sanitized query, and submits it to a configured cloud assistant (no proprietary data is shared).
5) Cloud reply is received by the Librarian, logged, redacted if needed, and then either:
    a) Sent back to the Researcher agent via IPC for immediate integration into the current response.
    b) Optionally ingested into the local RAG by the Librarian itself for provenance-aware responses and future local retrieval improvements (based on auto-update triggers).
6) Response is returned by the Researcher with provenance tags (local vs cloud) and logged.

Data flow (Librarian upkeep/management)
1) The Librarian runs continuously in the background.
2) It monitors the Researcher's activities (e.g., via state_manager ledger events) or acts on its own schedule/heuristics.
3) Based on configured auto-update triggers (e.g., consistently low retrieval confidence for certain topics, stale RAG data), the Librarian:
    a) Triggers re-ingestion of existing data sources.
    b) Triggers re-chunking/re-embedding of processed data.
    c) Initiates queries to cloud assistants to enrich the RAG store (e.g., for known gaps).
    d) Manages the creation or updates of specialized RAGs.
4) All Librarian actions are logged via the state_manager ledger for auditability.

Components
- **Researcher Agent (Main Process)**:
    - Local runtime: Ollama (phi3 mini/128k by default; can swap if hardware allows).
    - Embeddings + vector store: default `all-MiniLM-L6-v2` + FAISS index at `data/index/faiss.index` (configured in `config/local.yaml`) with SimpleIndex fallback and a separate mock index path to avoid format conflicts.
    - Document cache: raw docs under `data/raw/`, chunked/cleaned under `data/processed/`.
    - CLI/service: `researcher` CLI exposes `status`, `ingest`, `ask`, `plan`, `nudge`, and an interactive `chat` mode. Pipe-friendly via `--stdin`.
- **Librarian (Background Process)**:
    - A separate Python process (e.g., `librarian.py`) that runs alongside the Researcher.
    - Handles all direct interactions with cloud LLMs, ensuring strict sanitization and guardrails.
    - Manages RAG upkeep tasks, potentially leveraging the same `ingester` and `index` modules as the Researcher.
    - Communicates with the Researcher via a defined IPC mechanism (e.g., message queues, local sockets, or a shared database).
    - Logs its activities and decisions via the state_manager ledger.
- **Inter-Process Communication (IPC)**: A robust mechanism to enable seamless communication between the Researcher Agent and the Librarian process.
- Legacy artifact: sanitized Martin v5.1 reference at `martin_v5_1_reference.py` for reference/porting of UX behaviors.
- Guardrails: prompt sanitization, PII/proprietary redaction rules (implemented in `sanitize.py` and enforced by `cloud_bridge.py`), outbound allowlist, structured logging for every cloud call, provenance tags on answers.
- Cloud call logs: `logs/cloud/cloud.ndjson` (event-based entries with hashes and redaction flags).

Operational UX (Codex parity targets)
- **Execution controller**: enforces approval policy + sandbox mode; integrates command safety classifier for risky actions.
- **Agent mode**: `/agent on` auto-approves commands and fix steps, overriding interactive confirmations even when approval_policy is `on-request`.
- **Session manager**: persists chat state (plans, approvals, context refs) and supports resume.
- **Tool execution ledger**: structured, redacted record of tool calls and outputs for audit/export.
- **Diff UX**: unified/side-by-side diff viewer with paging, tied to edit workflow.
- **Context pack**: repo-aware summary (tree, tech stack, recent changes) with redaction and refresh.
- **Task queue**: persisted tasks with next-action prompts and idle reminders.
- **Local-only mode**: hard block on cloud calls with explicit warnings when cloud config is set.
- **Diagnostics**: Librarian health checks (socket ping, last error, cloud connectivity).
- **Librarian inbox**: background RAG notes and gap prompts surfaced to Martin for approve/ingest.

Storage/layout (proposed)
- `data/raw/`           incoming documents
- `data/processed/`     cleaned/chunked docs
- `data/index/`         vector DB / embeddings (shared access, potentially with locking, between Researcher and Librarian)
- `logs/`               local + cloud call logs (managed by state_manager ledger)
- `config/`             model + provider config (no secrets committed; shared by Researcher and Librarian)
- `.env`                local secrets (ignored); `.env.example` shows required vars
- `ipc/`                (new) potentially for IPC-related files or configurations.

Request/response schema (initial)
- Request: `{ "id": "<uuid>", "query": "<user text>", "mode": "ask|ingest", "context": { "tags": [], "files": [] }, "settings": { "k": 5, "cloud_allowed": false } }` (This is for Researcher CLI interaction)
- Librarian IPC Request: `{ "type": "cloud_query", "query": "<sanitized text>", "callback_id": "<uuid>" }`, or `{ "type": "ingest_request", "paths": ["..."] }`
- Librarian IPC Response: `{ "type": "cloud_query_response", "callback_id": "<uuid>", "result": { ...CloudCallResult... } }`, or `{ "type": "ingest_status", "success": true, "details": "..." }`
- Response: `{ "id": "<uuid>", "answer": "<text>", "provenance": { "local": [...], "cloud": [...] }, "confidence": { "local": 0.0, "cloud": 0.0 }, "logs_ref": "<path>" }` (This is for Researcher CLI output)
- Ingest result: `{ "id": "<uuid>", "ingested": [{"path": "...", "chunks": n}], "errors": [] }`

Sanitization/guardrails
- Strip/replace obvious secrets (keys, tokens, emails, file paths) before cloud calls.
- Outbound allowlist: only generic questions; block commands, file paths, and user identifiers.
- Log every cloud call with hashes of prompt/response (not raw content) plus timestamps via the state_manager ledger.

Test strategy (early)
- Unit tests: As before (sanitization/allowlist, request/response schema validation, FAISS index writer/reader, CLI args/stdin handling).
- Integration smoke:
    - Researcher CLI runs local phi3.
    - IPC mechanism: Test sending requests to Librarian and receiving responses.
    - Librarian lifecycle: Test starting, stopping, and its background tasks.
    - Librarian-triggered RAG updates: Verify that the Librarian can trigger and complete ingest/re-chunk operations.
    - Cloud calls via Librarian: Verify sanitized queries are sent and responses handled.
- Deterministic fixtures: Ship small sample docs for ingest tests; mock cloud calls for offline runs.
- Env safety: require API keys via `.env`/env vars; ensure tests do not read real keys.

CLI integration
- Researcher CLI exposes commands as before.
- Interactive `chat` mode uses IPC to communicate with the Librarian when cloud-based queries or RAG upkeep actions are needed.
- Commands like `ingest` could potentially send requests to the Librarian for background processing, or directly interact with RAG (depending on design).
- Returns answers with a compact provenance block: local hits + cloud snippets (from Librarian response).
