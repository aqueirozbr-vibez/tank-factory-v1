# Unit 31: Blocked, resume and restart basics

## Goal

One runner composes plan → approval → build and review → deliver for a
task the scheduler starts, enters at the point the trace says, posts the
Blocked-Comment when a phase stops without telling the human, and resumes
from `blocked` by the PRD's rule per `blocked_reason` (PRD §Capability 5 ·
Resume; TRD §7.8). A restart closes orphaned work and requeues running
tasks; resume never replays a side effect.

## Design

No UI. Visible result: a `factory:ready` issue goes to a PR with the App's
labels and comments only where the PRD names them; a block shows one
comment with the reason, the evidence and the next step; removing
`factory:needs-human` resumes per rule or puts the label back with the
reason.

## Implementation

### orchestrator/runner.py

- `RunnerDeps(writer, config, settings, harnesses per role, github,
  repo_clone, holdout, approvals, base_branch, token_for, sleep)`;
  `TaskRunner.run(task_id) -> RunReport`.
- Entry point from the trace: a recorded PR → deliver; an accepted verdict
  with no PR → deliver; no committed spec → plan; a previous block whose
  cause was a human omission (`open_questions`, `missing_holdout`,
  `human_decision_needed`, `spec_gate`) → plan after the cause check (a
  new issue comment after the block; the missing holdout ids present after
  a refresh; the spec blob on the branch differing from `tasks.spec_sha`);
  otherwise approval when the risk needs one, then build.
- A block: status, `Blocked-Comment` from the last failed gate row and
  `factory:needs-human`, unless the phase already notified (open
  questions, missing holdout, the review loop's human classes); an
  unexpected exception closes open work and blocks `timeout`.

### orchestrator/intake/resume.py

- `Resume.on_unlabeled(event)` for `factory:needs-human` removal: write
  access required (else `resume_refused` comment and the label back);
  `protected_path` and `dependency_change` never resume (comment, label
  back); `budget` needs a prior `budget_raise` control event; every other
  reason requeues with attempt + 1 and a `control` event, and the scheduler
  dispatches.

### orchestrator/watchdogs.py

- `sweep_on_start(writer)`: end orphaned processes, fail running phases and
  runs, requeue every `running` task with a `restart_requeue` control
  event.

### orchestrator/phases/plan.py

- A re-plan that rewrites the spec byte for byte: the unchanged claim is
  dropped from the diff check and logged (`replan: spec unchanged`).

### orchestrator/issue_to_pr.py

- `build_and_review` (inside an existing run, optional reviewer deps) and
  `run_build_and_review` (its own run); `PipelineOutcome.notified`.

### Other

- `RiskBreakdown.recorded(...)` rebuilds a breakdown from the task row for
  the approval wait after a restart; `TraceWriter.end_orphan_processes`;
  `messages.resume_refused`.

## Dependencies

- None new.

## Verify when done

- [ ] Green path: plan, risk, build, review, deliver rows in one run; PR
      opened once; no needs-human comment
- [ ] Repair-cap block posts one Blocked-Comment with the gate evidence and
      the label; a write-access removal requeues with attempt 2; a read
      actor is refused and the label returns
- [ ] Protected-path block never resumes; budget needs a raise
- [ ] Open questions: the plan phase's comment is the only one; resume
      without an answer stays blocked with one comment and no planner call;
      with an answer the task reaches `pr_open`
- [ ] Human decision: unchanged spec stays blocked; an edited spec re-plans
- [ ] Restart sweep mid-plan: process ended, phase failed, task requeued
      with attempt 2; the next run re-plans and opens the PR with no
      duplicate comment
- [ ] An identical spec rewrite passes the claim gates
- [ ] The four quality commands pass
