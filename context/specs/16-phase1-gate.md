# Unit 16: Phase 1 gate run

## Goal

Prove the PRD's phase 1 gate with evidence in the trace: 10 hand-written
specs produce 10 green branches with trace rows and cost; a seeded edit to
`tests/acceptance/` is rolled back and fails the phase 10 of 10 times; the
startup self-test rejects a placeholder command 100% of the time. The fake
harness drives the agent; the quality gates are the real ruff, mypy and
pytest. The same gate with the real Claude Code harness is a paid run the
operator starts by hand and records here.

## Design

Terminal only. Visible result: `tests/integration/test_phase1_gate.py`
green, and a recorded summary (branches, costs, incidents) in
`progress-tracker.md`.

## Implementation

### tests/integration/test_phase1_gate.py

- Seeds two scratch repositories with `seed_repository(...,
  with_violations=True)`.
- Gate 1: `run_spec` for issues 1 to 10 with the fake harness (the real
  self-test runs once); every run exits 0; every `agent/<n>` branch has
  one factory-authored commit; the trace holds 10 successful runs with
  cost > 0 and tool calls; the chain verifies.
- Gate 2: the ten `<n>-violation.json` scripts each end `blocked
  (protected_path)`, the worktree's holdout file is restored, and the
  trace holds 10 incidents.
- Gate 3: 20 self-test runs against a configuration with one placeholder
  command all refuse; the seeded configuration passes once.

### Manual real-harness run (operator, paid)

```
set FACTORY_CLAUDE_BIN=<path to claude.exe>
set ANTHROPIC_API_KEY=<key>
uv run factory dev seed C:\tmp\demo-real
uv run factory run-spec --repo C:\tmp\demo-real --issue 1 --harness claude_code
uv run factory trace --task dev/demo-real#1 --data-dir C:\tmp\demo-real\.factory-dev\data
```

Record the outcome, cost and any stream shape differences in
`progress-tracker.md`, and replace the hand-authored `claude_*.jsonl`
fixtures with the recorded stream.

## Dependencies

- None new.

## Verify when done

- [ ] Gate 1: 10 of 10 runs pass; 10 branches; 10 `success` runs; total
      cost equals the scripts' costs; chain verified
- [ ] Gate 2: 10 of 10 violation runs blocked `protected_path`; 10
      incidents; holdout restored each time
- [ ] Gate 3: 20 of 20 refusals; the seeded configuration passes
- [ ] The four quality commands pass
- [ ] `progress-tracker.md` records the fake-harness evidence and the
      pending real run
