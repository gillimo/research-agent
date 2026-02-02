OpenCode Migration Process (Current State)
=========================================

Goal
- Replace the existing CLI with OpenCode while preserving local-first guardrails, local model usage, and Researcher/RAG tooling.

What exists right now
- Local repo: `C:\Users\gilli\OneDrive\Desktop\projects\research_agent`
- OpenCode clone: `C:\Users\gilli\OneDrive\Desktop\projects\opencode`
- Local model runtime: Ollama installed, models present (phi3 variants).
- OpenCode config in repo: `.opencode.json` (local model + guardrails context)
- Guardrails context file: `opencode.local.md`
- Launcher script: `scripts/opencode_martin.ps1`

Current wiring (as implemented)
- OpenCode loads repo-local `.opencode.json` and uses:
  - `LOCAL_ENDPOINT=http://localhost:11434`
  - default agents set to `local.phi3:latest`
  - PowerShell shell path for command tool
- Guardrails are injected via `contextPaths`:
  - `opencode.local.md`
  - `AGENTS.md`
  - `docs/martin_operator_guide.md`
- Start OpenCode from this repo:
  - `.\scripts\opencode_martin.ps1`

Known gaps (next work)
- Fork/mirror OpenCode under `gillimo` (or vendor it) and document update flow.
- Add MCP bridge so OpenCode can call Researcher tools (ask/ingest/status/librarian).
- Migrate testing/verification to OpenCode flow.
- Migrate socket tooling to SocketBridge and validate.
- Fix local-only mode to run without Librarian active.
- Repair RAG ingestion in the new flow.

Notes
- OpenCode upstream repo is archived; plan whether to stay or move to its successor.
