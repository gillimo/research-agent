Task Queue (batch of 25)
========================

Legend: [ ] pending, [~] in progress, [x] done

1) [ ] Wire cloud bridge stub (provider/env parsing, allowlist gate, log to logs/cloud/).  
2) [ ] Add cloud cmd execution helper with prompt templating + redaction hashes.  
3) [ ] Integrate cloud hop into `ask` behind `--cloud-mode` flag with provenance merge.  
4) [ ] Add heuristic trigger for cloud hop (low top-k score threshold).  
5) [ ] Allow optional ingest of cloud snippets into index with provenance tag.  
6) [ ] Expand supervisor loop to keep coding agents nudged (configurable prompts).  
7) [ ] Add `martin.<ability>` dispatch (env.check, diagnose, plan.extract_commands, dev.create_file append-only).  
8) [ ] Expose abilities via CLI subcommand for testing (`python -m researcher abilities`).  
9) [ ] Add idle detector to `nudge` using recent log timestamps and last command.  
10) [ ] Enhance `plan --run` to separate stdout/stderr in logs and return non-zero on failure.  
11) [ ] Add cloud config doc section in `docs/setup_local.md` with env var names.  
12) [ ] Update README examples to show `--use-llm` and `--cloud-mode`.  
13) [ ] Add sample cloud log fixture and test for redaction hashes.  
14) [ ] Add test for provenance merge (local + synthetic cloud hit).  
15) [ ] Add test for heuristic cloud trigger (score threshold).  
16) [ ] Add config validation for index paths and create directories on load.  
17) [ ] Add CLI option to skip FAISS and force SimpleIndex for offline runs.  
18) [ ] Add timing stats to `status` (index load/query durations).  
19) [ ] Add cache for repeated `ask` queries (simple memo).  
20) [ ] Add packaging stub (`pyproject.toml` / `setup.cfg`) and update requirements.  
21) [ ] Add CI stub workflow (pytest only) with local paths disabled if missing deps.  
22) [ ] Add ingestion demo script that reindexes sample docs idempotently.  
23) [ ] Add error-handling polish: clearer messages and non-zero exits on failures.  
24) [ ] Add doc for supervisor/oversight behaviors and guardrails.  
25) [ ] Add question log entry for any blockers and close resolved items daily.
