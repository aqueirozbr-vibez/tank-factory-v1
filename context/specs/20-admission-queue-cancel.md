# Unit 20: Router, admission, queue and cancel

## Goal

Turn normalised events into factory state (PRD §Capability 1): only a
write-access human grants readiness; admission is idempotent on
`task_id = repo#issue`; the task row and workspace exist before anything
else happens; at most N tasks run, the rest wait in label order with a
comment; removing `factory:ready` stops the live process within 60 s,
marks the task `cancelled`, keeps the branch and names the last completed
phase.

## Design

No UI. Visible result: with a fake GitHub port and a local clone, a label
from a read-only user is removed with a comment and creates no task; 100
repeated labels create one task; with N = 1 the second task queues with
"1 ahead" and is promoted when the first finishes; a cancel ends a blocked
runner, kills a live child process, and comments the last phase.

## Implementation

### orchestrator/intake/ports.py, messages.py

- `GitHubPort` protocol: the five calls admission and cancel need.
- Comment texts for refusal, queue position and cancellation (unit 29
  replaces them with templates).

### orchestrator/supervisor.py

- `Supervisor(writer, runner, on_finished)`: one `asyncio.Task` per live
  task; `start`, `is_live`, `cancel(task_id, reason)` (cancel and await),
  `cancel_all(reason)` for the kill-switch later.

### orchestrator/intake/scheduler.py

- `Scheduler(writer, supervisor)`: `slots_free()` from
  `installation.concurrency_n` minus running tasks; `dispatch()` promotes
  the oldest queued tasks into `running` and starts them; `on_finished`
  schedules a dispatch.

### orchestrator/intake/admission.py

- `Admission.admit(event)`: actor check through the 5-minute
  `user_permissions` cache (bots and non-write actors refused: label
  removed, comment posted, no task); existing `queued`, `running`,
  `blocked` or `pr_open` task → `already_admitted` with no new run;
  `merged` → ignored; `cancelled` → re-queued as a new attempt on the kept
  branch; otherwise the task row, then the worktree, then the `log` event,
  then dispatch; a queued task gets the position comment.
- `Cancellation.cancel(event)`: supervisor cancel, open phases and runs
  closed as failed, `cancelled` status, `control` event with the actor and
  last completed phase, comment.

### orchestrator/intake/router.py

- `Router(writer, handlers).handle(event)` routes labels, unlabels,
  merges, reviews, CI results and pushes to their handlers; unhandled
  routes write a `log` event; every handled delivery is marked processed.
  `run(inbox, stop)` drains the inbox.

### orchestrator/harness/process.py

- `run_with_timeout` terminates the process tree when the awaiting task is
  cancelled, then re-raises.

### Writer additions

- `cache_permission`, `cached_permission`, `count_running`,
  `oldest_queued`, `queue_position`, `requeue_task`,
  `last_completed_phase`, `close_open_work`.

## Dependencies

- None new.

## Verify when done

- [ ] Read-only actor and `[bot]` actor: label removed, comment posted, no
      task row; the permission lookup is cached for 5 minutes
- [ ] 100 repeated `factory:ready` events: one task, one worktree, no run
- [ ] N = 1: first task `running`, second `queued` with a "1 ahead"
      comment; when the first finishes the second is promoted
- [ ] Cancel of a live task: runner cancelled, status `cancelled`, branch
      kept, `control` event, comment naming the last completed phase; cancel
      of a queued task works without a runner; re-labelling starts attempt 2
- [ ] A cancelled `run_with_timeout` kills its child process tree
- [ ] Unhandled event kinds write a `log` event and mark the delivery
      processed
- [ ] The four quality commands pass
