Bug Log
=======

- 2025-12-29: TUI preflight f-string syntax error in `researcher/tui_shell.py` caused pytest collection failure; fixed.
- 2025-12-19: Agent lacks conversational context retention, gets sidetracked easily, and provides unhelpful responses due to apparent lack of understanding. This is a critical usability issue for the CLI tool.
- 2025-12-18: Sanitization regexes not redacting email/path; fixed patterns for emails and Windows paths in `researcher/sanitize.py`.
- 2025-12-18: Tests failed initially due to missing pytest install; added `requirements.txt` and installed dependencies.
- 2025-12-18: Pydantic regex deprecated; switched to `pattern` in `schemas.py`. Added ingestion/chunking path and logging stub; pytest suite now green.
- 2025-12-18: Expanded CLI ingestion/logging/provenance; added supervisor stub and rotating logs; tests extended for ingest pipeline (7 passing).
- 2025-12-18: FAISS/embedding load failed for private HF model (`bge-small-en-v1.5`); default embedding changed to public `all-MiniLM-L6-v2`, added fallback to SimpleIndex, and separate mock index path to avoid format conflicts.
