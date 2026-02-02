# Plan of Attack (Piccilo)

Goal: train Martin to Codex-level behavior for complex tasks: break down, remember operating instructions, execute, report, and continue.

Principles
- Action-first: plan, execute, summarize, then continue.
- Minimal questions: ask only when blocked; state assumptions.
- Persistent operating rules: inject them every turn.
- Queue and chain tasks: multi-step requests are handled end-to-end.
- Transparent progress: live status + concise summaries.

Phase 1: Behavior contract
- Freeze a behavior contract in docs and map each item to a test.
- Include complex-task expectations: decomposition, queueing, chaining, checkpoints.
- Define success output templates for plan, execution, and summary.

Phase 2: Planner + queue executor
- Use a sanitized mini planner to produce a 3–7 step action queue.
- Store and surface the queue; advance automatically after each step.
- Require output summaries after each command and a "what’s next" line.
- Gate any risky steps with approvals and continue after confirmation.

Phase 3: Operating instruction memory
- Persist "how to operate" rules in state.
- Inject the rules into each prompt as a compact context block.
- Add a /rules command to view and refresh them.

Phase 4: Behavior UAT
- Add a "complex task" scenario to the UAT harness.
- Assertions: plan produced, queue stored, step execution, summaries, next-step prompt.
- Run UAT on each behavior change and log regressions in bug log.

Phase 5: Feedback loop
- Capture good/bad transcripts and tag behavior deltas.
- Update the behavior contract and tests from real failures.
- Keep parity notes updated in expected behavior and UX docs.

Immediate tasks
1) Wire queue executor to auto-advance (stop on blocking or approval).
2) Add complex-task UAT scenario and run it.
3) Add persistent operating rules block and /rules.
4) Ensure output summaries appear after all tool runs.

Definition of done
- Complex requests finish end-to-end without manual babysitting.
- Martin reports progress and next steps, every time.
- UAT passes for all behavior scenarios, including complex tasks.
