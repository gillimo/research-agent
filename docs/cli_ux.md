CLI UX Guide
============

Entry point
- Run from repo root with Python: `python -m researcher <command> [options]`.
- Commands: `status`, `ingest`, `ask`, `plan`, `nudge`.
- Pipe-friendly: add `--stdin` to read prompt text from standard input.

Recommended flow
1) Status check: `python -m researcher status`  
   Shows local model, embedding model, index path/type, and doc count.
2) Ingest sample: `python -m researcher ingest data/sample/readme.txt`  
   Builds/updates the FAISS index (falls back to SimpleIndex if needed).
3) Ask locally: `echo "your question" | python -m researcher ask --stdin [-k 5] [--use-llm]`  
   Prints a provenance table (score/source/chunk). `--use-llm` forces local LLM synthesis from retrieved context.
4) Plan/run commands: `echo "command: dir" | python -m researcher plan --stdin --run`  
   Shows sanitized prompt, extracted plan, and per-command status/output.
5) Nudge/oversight: `python -m researcher nudge [--idle-seconds 300]`  
   Emits a nudge if logs show idleness beyond the threshold.

UX cues
- Tables use Rich for readable columns and headers.
- `ask` output shows provenance first (local hits), then stderr notes if sanitization altered input.
- `plan --run` reports `OK`/`FAIL(code)` per command with captured stdout/stderr.
- Logs for all commands go to `logs/local.log` (rotating).

Optional cloud bridge (until integrated into `ask`)
- `echo "prompt" | python scripts/researcher_bridge.py --stdin --cloud-mode always --cloud-cmd "$env:CLOUD_CMD"`
- Set `CLOUD_CMD` to your preferred CLI (e.g., codex/gemini/llm) that accepts `{prompt}`.
- Inline cloud hop from CLI: `echo "prompt" | python -m researcher ask --stdin --cloud-mode always --cloud-cmd "$env:CLOUD_CMD"` (sanitized, logged to `logs/cloud/cloud.log`).

Notes
- Secrets stay in `.env` (gitignored). `OPENAI_API_KEY` only needed for the Martin artifact or future cloud hop.
- Local LLM defaults: Ollama `phi3` at `http://localhost:11434` (configurable in `config/local.yaml`).
