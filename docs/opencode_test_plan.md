OpenCode Migration Test Plan
============================

Prereqs
- Ollama running with `phi3:latest`
- OpenCode repo present at `projects/opencode`

1) Build OpenCode (if needed)
   - `scripts\\build_opencode.ps1`

2) Launch OpenCode in repo
   - `scripts\\opencode_martin.ps1`

3) MCP tool sanity check
   - Ask OpenCode to run the Researcher MCP tool:
     - Example prompt: "Use the researcher_ask tool to query: 'what is in docs/architecture.md?'"
   - Confirm the tool responds with JSON from `researcher ask --json`.

4) Ingest smoke
   - Example prompt: "Use researcher_ingest on docs/architecture.md"
   - Confirm ingest reports ok.

5) Status check
   - Example prompt: "Use researcher_status"
   - Confirm JSON status returns.

6) SocketBridge IPC smoke (optional)
   - Run `python scripts\\socketbridge_smoke.py`

7) Local-only flow
   - Ensure `cloud.enabled=false` and `local_only=true` in `config\\local.yaml`
   - Verify OpenCode can still query local model without Librarian.
