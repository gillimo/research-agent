Research Agent Workspace
========================

Two-agent system: a local "researcher" that owns the RAG store and a cloud assistant consulted through sanitized queries for breadth/recall.

Current stack
- Local model runtime: Ollama, with `phi3` (mini / mini-128k) already installed in `C:\Users\gilli\.ollama`.
- Embeddings/vector store: default `all-MiniLM-L6-v2` + FAISS index (`config/local.yaml`) with SimpleIndex fallback if model is unavailable.
- Interface: pipe-friendly CLI bridge at `scripts/researcher_bridge.py` to route through local then optional cloud CLI (Codex/Gemini/llm/etc.).
- Artifact: sanitized Martin v1.4.7 reference at `martin_v1_4_7.py` (requires `OPENAI_API_KEY` in `.env`).

Quick start (bridge)
- Verify local model: `ollama list` should show `phi3`.
- Run from repo root: `echo "test prompt" | python scripts/researcher_bridge.py --stdin`.
- To add cloud hop, set `CLOUD_CMD='codex --model gpt-4o --prompt "{prompt}"'` (or Gemini/llm) and pass `--cloud-mode always`.
- Copy `.env.example` to `.env` and set `OPENAI_API_KEY` if you plan to run the Martin artifact or cloud hops.
- Researcher CLI (FAISS default): `python -m researcher ingest data/sample/readme.txt`, then `echo "query" | python -m researcher ask --stdin` for local retrieval with provenance table.

Project references
- `PROJECT_PLAN.md`: milestones and open decisions.
- `docs/architecture.md`: system diagram, data flow, and guardrails.
- `docs/setup_local.md`: local setup, model/embedding notes, and run commands.
- `docs/tickets.md`: initial ticket backlog to implement the MVP.
