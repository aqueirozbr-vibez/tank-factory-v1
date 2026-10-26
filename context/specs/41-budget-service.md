# Unit 41: Budget service

## Goal

The per-task budget derives at install from a 3-issue dry run at 2× the
highest observed cost (fewer than 2 of 3 completions refuse to go live),
recorded in the install log; the daily cap follows N × 3 × the per-task
budget by trigger; a phase does not start past the task or daily cap; a
10 s spend watchdog SIGTERMs a phase at the cap; a cap breach blocks the
task with `factory:budget` and a spend comment; a write-access user may
raise one task's budget once, by at most 2×, with a reason, and resume it;
the daily cap holds the queue until the day rolls over (PRD §Capability 5
· Budgets, Resume; TRD §6.1, §6.3, §4.4 install log).

## Design

Terminal only. The between-phase check already existed (`check_budget`);
this unit adds the in-phase watchdog, the notification, the raise, the
queue hold and the dry run. `SpendGuard` wraps every proxied agent call:
it reads the key's spend from the proxy every `SPEND_WATCHDOG_INTERVAL_S`,
projects the task's and today's totals, and at a cap writes `budget_stop`
and cancels the harness call; the runner's cancellation path terminates the
process tree and, in a container, kills the container, and the phase
reports `error_max_budget_usd`, so the existing branches block the task for
budget. The runner's notification posts the spend comment and the
`factory:budget` label (plus `factory:needs-human`, the resume gesture).
`raise_task_budget` is one control event (`budget_raise`) that the resume
rule already requires; resuming removes `factory:budget`. The scheduler
dispatches nothing while today's spend has reached the daily cap and logs
the hold once per day. `factory dry-run` replays three issues through the
build slice, derives the budget, writes `per_task_budget_usd` in `install`
mode (the trigger recomputes the daily cap) and appends the derivation to
`install.log` on the data volume; it refuses, writing nothing to the
installation row, below two completions or at zero observed cost.

## Implementation

- `orchestrator/budget/watch.py`: `SpendGuard`, `SpendBreach`,
  `run_guarded`; `PhaseKeys.guard(issued, ...)`; the three agent phases
  call `run_guarded` and check the budget error before the timeout branch.
- `orchestrator/budget/controls.py`: `raise_task_budget`, `RaiseOutcome`.
- `orchestrator/budget/dry_run.py`: `DryRunIssue`, `Derivation`,
  `derive_budget`, `InstallLog`.
- `orchestrator/api/app.py`: `POST /tasks/{task_id}/budget` (loopback
  callers with `X-Factory-Actor` until the console token lands; 400, 403,
  404, 409 already raised, 422 not an increase or over 2×).
- `cli/budget.py`: `factory budget --task --raise-to --reason [--by]`,
  `factory dry-run --repo --issues a b c [--harness] [--data-dir] [--org]
  [--skip-self-test]`.
- Runner `_notify` for budget; `Resume` removes `factory:budget` on resume;
  `Scheduler(clock=...)` daily hold; `DockerCli.exec_runner` kills the
  container on cancellation; writer `set_task_budget`,
  `latest_budget_stop`; constants `DRY_RUN_ISSUES`,
  `DRY_RUN_MIN_COMPLETE`, `DRY_RUN_PROVISIONAL_BUDGET_USD`; the budget
  comment names the raise command and the resume gesture.

## Dependencies

- Units 35 and 40. No new package.

## Verify when done

- [ ] A fake phase whose key reports spend past the task budget is stopped
      within the bound, writes `budget_stop` and blocks the task for budget
      with the proxy's spend as the phase cost; the daily cap trips the same
      way
- [ ] A second raise is refused, as are a non-increase and a raise above 2×;
      a valid raise writes one control and lets the resume rule pass
- [ ] The daily cap holds the queue until the day rolls over under a
      controlled clock
- [ ] `derive_budget` refuses below two completions and at zero cost;
      `factory dry-run` on the seeded repository derives 2× the highest cost,
      recomputes the daily cap and appends to the install log
- [ ] The four quality commands pass
