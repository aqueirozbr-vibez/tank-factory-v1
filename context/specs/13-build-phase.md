# Unit 13: Build phase state machine

## Goal

Run the phase 1 slice of `issue_to_pr`: for a task with a spec and a
worktree, `build` → `code_gates` → `commit`, with every rule of PRD
§Capability 3 in code: the builder gets one prompt and returns one
envelope; code checks every claim; a protected-path change ends the task;
failures go back into the same session as a correction at most three
times; infra errors retry without consuming repairs; the fourth failure
blocks with evidence; code commits.

## Design

No UI. Visible result: with the fake harness on a temp repository, the
green script commits and the run is `success`; a wrong claim costs one
repair; four failures block with `repair_cap`; a protected edit blocks at
once with `protected_path` and an incident; a timeout, a budget stop and a
max-turns stop each block with their reason. Every transition is a row and
the chain verifies.

## Implementation

### orchestrator/phases/build.py

- `parse_focused_tests(spec_text)`: test ids under `## Test plan`.
- `verify_from(gate_result) -> VerifyOutput` for the repair prompt.
- `BuildDeps(writer, config, settings, harness, sleep, factory_version,
  runner)` and `BuildOutcome(status, run_id, commit_sha, blocked_reason,
  evidence, repairs, builder_calls, cost_usd)`.
- `BuildPhase.run()`: one `build` phase row per builder call (attempt n),
  one `code_gates` row per round, one `commit` row at the end. Per round:
  prompt from unit 12 (with `previous_verify` on repair, same session id);
  `agent_start` and `agent_end` events carrying guide sha, harness version,
  model, usage and cost; tool calls through the trace sink; then:
  - harness `timeout` or `killed` → blocked `timeout`;
  - harness `error`: `error_max_budget_usd` → blocked `budget`;
    `error_max_turns` → blocked `repair_cap`;
    `error_max_structured_output_retries` → envelope failure; anything
    else → infra retry with backoff 5, 20, 60 s, at most 3, then blocked
    `timeout`;
  - `envelope_schema` fail → one re-prompt in the same session, then
    blocked `repair_cap`;
  - `permissions_enforce` fail → rollback, incident, blocked with the
    outcome's reason, no repair;
  - claim gates then quality gates → first failure becomes the next
    round's `VerifyOutput`; after `REPAIR_CAP` repairs, blocked
    `repair_cap`;
  - all green → `commit` phase, `commit_all` with the envelope's message,
    run `success`.

### orchestrator/issue_to_pr.py

- `run_build_slice(deps, task_id, worktree, spec_rel, attempt)`: starts
  the run, sets the task `running`, runs `BuildPhase`, finishes the run.

### orchestrator/harness/registry.py

- `make_harness(role_config, settings, fake_scripts)` for `fake` and
  `claude_code` (Codex arrives in unit 26).

### Settings and writer

- `Settings.agent_env()`: the base env plus a phase 1 pass-through of
  `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `OPENAI_API_KEY`,
  `OPENAI_BASE_URL`, replaced by the proxy's virtual key in phase 3.
- `TraceWriter.set_process_pid`.

### fixtures/specs/418-invoice-due-date.md

- A hand-written spec in the seven-section template.

## Dependencies

- None new.

## Verify when done

- [ ] Green: run `success`, a commit on `agent/418` authored by the
      factory, phases `build`, `code_gates`, `commit` all `pass`, gate rows
      for `envelope_schema`, `permissions_enforce`, the three claim gates
      and the four quality gates, cost 0.41 on the task, chain verifies
- [ ] Wrong claim then fix: one repair, the second request resumes the
      first session and carries "Previous verification result", two `build`
      rows with attempts 1 and 2
- [ ] Always wrong: four builder calls, then blocked `repair_cap`, run
      `fail`
- [ ] Protected edit: one call, rollback, incident event, blocked
      `protected_path`; a manifest edit blocks `dependency_change`
- [ ] Invalid envelope then valid: one re-prompt; two invalid: blocked
      `repair_cap` after two calls
- [ ] Sleeping script under a 1 s role timeout: blocked `timeout`
- [ ] Infra error then green: one retry, `sleep(5)` called, pass; four
      infra errors: blocked `timeout` with sleeps 5, 20, 60
- [ ] `error_max_budget_usd` → blocked `budget`; `error_max_turns` →
      blocked `repair_cap`
- [ ] The four quality commands pass
