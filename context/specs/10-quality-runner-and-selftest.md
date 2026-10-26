# Unit 10: Quality runner and startup self-test

## Goal

Run the installation's quality commands by code in the PRD's order
(format, lint, typecheck, focused tests), stop at the first failure, and
keep the command, exit code and the last 4,000 characters as evidence. At
start-up, run every configured command against a known-failing fixture and
refuse to start if any of them exits 0, so a placeholder gate never runs.

## Design

No UI. Visible result: scripted commands produce ordered `GateResult`s with
short-circuit at the first failure; the self-test rejects a command that
always exits 0, 20 of 20 times; the real default commands all fail inside
`fixtures/known_failing/`.

## Implementation

### orchestrator/gates/quality.py

- `run_quality(worktree, commands, env, focused_tests, timeout_s, runner)`
  → `QualityOutcome(results, passed, first_failure)`. Each gate is one
  subprocess with an explicit argv (focused test ids appended to `test`);
  stdout and stderr tails are kept and the combined tail is capped at
  4,000 characters; a non-zero exit, a timeout or a missing binary fails
  the gate and the runner stops. Gates after a failure are not run (the
  ladder shows them as skipped).
- `run_full_suite(worktree, commands, env, timeout_s, runner)` → one
  `full_suite` `GateResult` (used by the full-suite phase later).

### orchestrator/gates/selftest.py

- `self_test(commands, fixture_dir, env, timeout_s, runner)` →
  `SelfTestReport(ok, checks)` running `format`, `lint`, `typecheck`,
  `test` and `full_suite` in the fixture directory; a command passes the
  self-test only when it is found and exits non-zero.
- `assert_self_test(report)` raises `ConfigError` naming the offending
  command; the orchestrator start-up calls it before admitting anything.

### fixtures/known_failing/

- `broken.py` (badly formatted, unused import, wrong return type) and
  `test_broken.py` (one failing test). Excluded from the factory's own ruff
  run; mypy and pytest never reach it.

## Dependencies

- None new.

## Verify when done

- [ ] With scripted commands that all exit 0, four passing results in
      order, each with the command and exit code
- [ ] With `lint` exiting 1, results are `format` pass and `lint` fail and
      nothing else ran; the failing result carries the output tail
- [ ] Focused test ids are appended to the `test` argv
- [ ] A command that sleeps past the timeout fails with the timeout noted
- [ ] A missing binary fails the gate and names the command
- [ ] The self-test marks a command that exits 0 as a placeholder and
      `assert_self_test` raises naming it, 20 of 20 runs
- [ ] A missing binary fails the self-test
- [ ] The real default `quality.yaml` commands all exit non-zero inside
      `fixtures/known_failing/` (integration test)
- [ ] The four quality commands pass
