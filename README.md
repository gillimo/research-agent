# Research Agent Workspace

Mission Learning Statement
- Mission: Build a two-system research architecture with a local agent and a RAG-backed librarian.
- Learning focus: retrieval pipelines, memory management, tool orchestration, and safe cloud handoffs.
- Project start date: 2025-12-18 (inferred from earliest git commit)

Two-agent system: a local agent consumes a RAG store built by the researcher, with optional cloud assistance for breadth and recall.

## Features

- Local RAG ingestion and retrieval (FAISS with SimpleIndex fallback)
- CLI with status/ingest/ask/plan/supervise commands
- Interactive chat and TUI shells
- Optional cloud hops with approval gating

## Installation

### Requirements

- Python 3.10+
- Ollama (local model runtime)

### Setup

- Copy `.env.example` to `.env` and set `OPENAI_API_KEY` if cloud hops are enabled.
- Verify local model: `ollama list` should show `phi3`.

## Quick Start

```bash
python -m researcher status
python -m researcher ingest data/sample/readme.txt
python -m researcher ask --stdin
```

## Usage

Core commands:
- `python -m researcher status [--json]`
- `python -m researcher ingest <path> [--simple-index]`
- `echo "query" | python -m researcher ask --stdin`
- `python -m researcher plan --stdin [--run]`
- `python -m researcher supervise --idle-seconds 300 --sleep-seconds 30`

Interactive sessions:
- `python -m researcher chat`
- `python -m researcher tui`

## Architecture

```
Sources -> Ingest -> Index (FAISS/SimpleIndex) -> Retrieve
                         |                        |
                         v                        v
                     Librarian               Local Agent
                         |                        |
                         v                        v
                    Cloud Assistant <---- Orchestrator
                         |
                         v
                      Responses
```

## Project Structure

```
researcher/        # Core package
config/            # Local config
scripts/           # Utility scripts
logs/              # Run logs
```

## Building

No build step required. Run directly with Python.

## Contributing

See `docs/tickets.md` for the backlog and `PROJECT_PLAN.md` for milestones.

## License

No license file is included in this repository.
