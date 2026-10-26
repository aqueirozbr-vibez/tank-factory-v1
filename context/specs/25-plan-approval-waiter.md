# Unit 25: Plan approval waiter

## Goal

`risk:high` posts the spec for review and waits for `factory:plan-approved`
from a user with write access; `risk:governed` counts the label only from a
CODEOWNER of the governed path (PRD §Capability 2). The label is the only
approval record: the `approvals` row copies the actor and the GitHub event
id and binds to the task's current spec sha, so a spec edit after approval
needs a new approval, and a label that predates the current spec commit is
recorded and ignored with a comment (TRD §6.2).

## Design

No UI. Visible result on GitHub: the plan-review comment with the risk
equation and the spec, `factory:plan-review` while waiting, a comment and
label removal for an ignored approval, and the build starting after a valid
one.

## Implementation

### migrations/0004_task_approval_binding.sql

- `tasks.spec_committed_at` (set by `set_task_spec` when the sha is bound)
  and `tasks.governed_path` (set by `set_task_risk`).

### orchestrator/phases/approval.py

- `gate_for(risk)`: `risk:high` → `plan`, `risk:governed` → `governed`,
  else none.
- `PlanApprovals(writer, github, repo_clone)`: the router's `plan_approved`
  handler. `on_label(event)` judges every label event, live waiter or not:
  not awaiting (unknown task, no spec sha, `risk:low`, finished) → log;
  stale (GitHub's own time, or the receipt time, before
  `spec_committed_at`) → recorded, comment, label removed; actor without
  write access → same; governed and actor not a CODEOWNER of
  `tasks.governed_path` → same, naming the owners; otherwise an `approvals`
  row bound to `tasks.spec_sha` and an `approval` event under the login;
  a second label on the same sha is a duplicate, counted silently. A live
  waiter registered for the task receives every verdict.
- `PlanApprovalPhase`: inside the task. `risk:low` → `not_required`.
  Otherwise a `plan_approval` phase row; an existing row covering the sha
  passes at once (restart); else the plan-review comment and
  `factory:plan-review`, then wait for a counted verdict on this sha,
  remove `factory:plan-review`, pass.
- CODEOWNERS is read from the clone's default branch (`CODEOWNERS`,
  `.github/CODEOWNERS`, `docs/CODEOWNERS`); `is_owner` matches `@login`
  case-insensitively. Team owners (`@org/team`) are not resolved in v1.

### Shared pieces

- `orchestrator/phases/recorder.py`: `PhaseRecorder` (phase rows, start and
  end events, the sequence counter); the first user, `BuildPhase` and
  `PlanPhase` move to it in the scheduled refactor.
- `orchestrator/intake/actors.py`: `actor_permission` (the cached sensor
  admission already used), shared by admission and approvals.
- `NormalisedEvent.occurred_at`: GitHub's timestamp when the poller
  reconstructs an issue event, so a replayed old label is still stale.
- `RiskBreakdown.governed_path`: the tier A path (or the first Scope path
  when tier A came from the planner alone).

## Dependencies

- None new.

## Verify when done

- [ ] High-risk task: plan-review comment and label; a `read` actor is
      ignored with a comment and the label removed; a `write` actor is
      recorded (actor, event id, bound sha, phase id) and starts the build
- [ ] A label before the spec commit (webhook time or GitHub's own time) is
      ignored
- [ ] Governed: a write-access non-owner is ignored naming the owners; the
      CODEOWNER is recorded with `codeowner = 1` and the path
- [ ] An approval for sha A does not cover sha B; a restarted waiter with a
      covering row passes without commenting
- [ ] `risk:low` and unknown issues are not awaiting; the router delivers
      the label to the service
- [ ] The four quality commands pass
