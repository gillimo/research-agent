Martin → Research Agent Alignment
=================================

Goal
- Replicate the useful Martin UX (command plans, diagnoses, progress summaries) using a local model as the “researcher” and a cloud “librarian” only for sanitized augmentation/training of the RAG store.

Key Martin behaviors to port
- Command extraction: detect `command:` lines, build a plan, and execute with per-step status.
- Smart runner: choose interactive vs non-interactive paths; timeouts; transcript capture.
- Diagnosis loop: on failure, ask the model to suggest fixes, extract commands, optionally execute.
- Summaries/heartbeats: periodic progress summaries from captured output.
- Rephraser: friendly/concise user messaging.

Adaptation for local + cloud librarian
- Local model (phi3 via Ollama) runs orchestration: plan, run commands, diagnose, and ingest results into RAG.
- Cloud librarian is only called with sanitized prompts when extra guidance is needed; its responses are logged and can be used to improve retrieval (e.g., add snippets or embeddings to the store).
- Provenance is kept on every answer and stored chunk so we can distinguish local vs cloud-derived data.

Planned CLI shape
- `researcher ask --stdin --provenance`: local-first answer; cloud call optional via `--cloud-mode always` or heuristic trigger.
- `researcher ingest <files>`: chunk + embed + index; mark source and whether it came from cloud guidance.
- `researcher status`: show model info, index stats, and last cloud calls.
- Pipe-friendly: `echo "cmd?" | researcher ask --stdin`.

Immediate safety/cleanup
- Strip any embedded API keys in legacy Martin files (e.g., `Project Blackbox/offload package/Martin.py`) and rely on env vars.
- Ensure sanitization/allowlist rules before enabling cloud calls by default.
