# Unit 34: CI re-entry and the ci_wait phase

## Goal

A failing `check_run` or `workflow_run` on the PR's head, or a review with
`changes_requested`, re-enters the build phase on the same branch and PR
within the same repair cap; a merge sets `merged`; `pr_number` is reused
and a second PR is never opened (PRD §Capability 5; TRD §4.5, §7.5).

## Design

No UI. Visible result: after delivery the run holds a `ci_wait` row owned
by GitHub that stays running; a CI failure fails it with the conclusion, a
`ci_reentry` control event is written and the task is requeued; the next
run enters at build with the failure as its previous verification result,
reviews again, pushes the fix to the same branch and reuses the PR; the
merge closes the `ci_wait` row and the task.

## Implementation

### orchestrator/intake/ci.py

- `CiReentry(writer, scheduler)`: `on_ci_failed` (conclusions `failure`,
  `timed_out`, `cancelled`, `action_required`) and `on_changes_requested`.
  The task is found by PR number or branch and must be `pr_open`; a result
  for a sha other than `tasks.pr_head_sha` is stale and ignored; a second
  signal while a re-entry is pending is ignored; otherwise the open
  `ci_wait` row fails with the cause, a `ci_reentry` control event records
  cause, detail, PR, sha and GitHub event id, the task is requeued and the
  scheduler dispatches.
- `pending_reentry(writer, task_id)`: the latest `ci_reentry` control
  newer than the last delivery handoff.

### orchestrator/runner.py

- A task with a PR enters at build when a re-entry is pending, else at
  deliver. The re-entry build receives `VerifyOutput(gate="ci", ...)` as
  its previous verification result and `repairs_used` = the number of
  `ci_reentry` controls since the last `resume` control; more than
  `REPAIR_CAP` blocks `repair_cap` with the Blocked-Comment before any
  builder call. A resume gives fresh caps, as the PRD's rule says.

### orchestrator/phases/build.py and issue_to_pr.py

- `BuildPhase(..., initial_verify=None, repairs_used=0)`;
  `build_and_review(..., initial_verify, repairs_used)`.

### orchestrator/phases/deliver.py

- After `pr_open`, a `ci_wait` row (kind engineer, owner `github`) when none
  is open; `MergeObserver` passes it on merge.

### Writer

- `open_phase`, `finish_open_phase`, `latest_control`, `latest_handoff_seq`,
  `count_controls`.

## Dependencies

- None new. The human review's comments do not reach the builder in v1;
  the builder learns that changes were requested and by whom.

## Verify when done

- [ ] After delivery a running `ci_wait` row exists; a failing check run on
      the head sha requeues the task once; a stale sha and a second signal
      are ignored; the next run enters at build, the builder prompt carries
      the CI failure, the PR number is unchanged, the branch is pushed
      again, a new `ci_wait` row is open
- [ ] `changes_requested` through the router re-enters the same way
- [ ] A fourth re-entry blocks `repair_cap` without a builder call; after a
      resume the build runs with fresh caps
- [ ] A merge passes the `ci_wait` row and sets `merged`
- [ ] The four quality commands pass
