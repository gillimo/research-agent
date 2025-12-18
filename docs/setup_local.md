Setup (Local)
=============

Prereqs
- Windows with PowerShell.
- Ollama installed (present) and `phi3` downloaded (`ollama list` to verify).
- Python 3.11+ recommended for the CLI/service (will add requirements once code lands).
- Secrets/config: copy `.env.example` to `.env` and set `OPENAI_API_KEY` (required for Martin artifact/cloud), `MARTIN_MODEL_MAIN`, `MARTIN_MODEL_MINI` as needed.
- Optional: Hugging Face token only if you switch to gated embedding models; default embedding is public (`all-MiniLM-L6-v2`).

Environment layout
- Clone/open `C:\Users\gilli\OneDrive\Desktop\research_agent`.
- Data dirs (create as needed): `data/raw`, `data/processed`, `data/index`, `logs`.
- Config (planned): `config/local.yaml` for model/provider choices; secrets via env vars.

Suggested Python env
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Model checks
```powershell
ollama list           # should show phi3
ollama run phi3 "hi"  # quick smoke test
```

Planned commands (once implemented)
- Current bridge: `echo "question" | python scripts/researcher_bridge.py --stdin`
- Add cloud hop (example Codex): `$env:CLOUD_CMD='codex --model gpt-4o --prompt "{prompt}"' ; echo "question" | python scripts/researcher_bridge.py --stdin --cloud-mode always`
- Researcher CLI (mock index now): `python -m researcher status`, `python -m researcher ingest data/sample/readme.txt`, `echo "test" | python -m researcher ask --stdin`
- Default vector store uses FAISS; if HF model download fails, CLI falls back to SimpleIndex (`mock_index_path`). To avoid 401s, keep default embedding or provide HF auth for private models.
- Logs: `logs/local.log` (rotating) captures ask/ingest activity.

Notes
- Embedding model/vector DB selection is still open; defaults will be set in `config/local.yaml`.
- Cloud credentials (e.g., `CLOUD_MODEL`, `CLOUD_API_KEY`) will be read from env; do not commit keys.
- OPENAI_API_KEY is required for the Martin artifact (`martin_v1_4_7.py`) if you run it; keep it in `.env` (ignored by git).
