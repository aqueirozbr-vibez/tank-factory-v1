# Unit 39: Full suite with the holdout

## Goal

Code runs the full suite, including the holdout acceptance tests the
builder never saw, on the final SHA before any PR; a failure starts one
more builder repair counted against the cap, then blocks the task with
the failing tests (PRD §Capability 5 · Requirements; TRD §7.5, §3.1 "the
holdout checkout is never mounted into a task container; the full-suite
container gets it read-only").

## Design

Terminal only. `FullSuitePhase` runs between an accepted review and
delivery, and again whenever a run enters at the deliver point, so the
suite always ran on the SHA that gets pushed. Worktree mode copies
`tests/acceptance` from the holdout checkout into the worktree for the
phase alone and restores the tree with `git checkout`/`git clean` after;
container mode creates a fresh container from the task image with the
holdout's `tests/acceptance` bind-mounted read-only over `/work`, removes
it after, and never mounts the hook bundle or hands it a key. The
environment is the base allow-list only. A failure buys one builder repair
(`FULL_SUITE_REPAIR_CAP = 1`) only while the attempt's repair cap has
room; the repair runs in the builder's own workspace (which still has no
holdout) through `BuildPhase` with a `VerifyOutput` naming the failing
tests first, then the suite runs again; a second failure blocks with
`full_suite`, and the comment carries the failing test ids and the
command's last output.

## Implementation

### orchestrator/phases/full_suite.py

- `FullSuitePhase(deps, task_id, worktree, spec_rel, run_id, holdout,
  issue_number, attempt, build_repairs, docker, task_image, network)`,
  `FullSuiteOutcome(status, run_id, head_sha, repairs, failing_tests,
  blocked_reason, evidence)`, `failing_tests(output)` (pytest `FAILED` and
  `ERROR` summary lines), `holdout_copied` context manager,
  `repair_request(result, failing)`, `HoldoutSource` protocol.

### Wiring

- `HoldoutPort` gains `tests_dir` and `copy_into`; `create_container`
  takes `extra_mounts`; the runner runs the phase before `DeliverPhase`
  and maps a block to `blocked(full_suite)` in phase `full_suite`.

## Dependencies

- Units 36 and 21. No new package.

## Verify when done

- [ ] A holdout test that fails on the built tree triggers one counted
      builder repair; when the repair fixes it the suite passes on the new
      SHA; when it does not, the task blocks with `full_suite` and the
      evidence names the failing test
- [ ] With the repair cap already spent the suite failure blocks at once
- [ ] The worktree carries no holdout file after the phase, in both outcomes
- [ ] In container mode the builder's container has no holdout mount, the
      suite container mounts it read-only, carries no hook bundle and no
      key, and is removed afterwards
- [ ] The green runner path records `full_suite` between `review` and
      `deliver`; the four quality commands pass
