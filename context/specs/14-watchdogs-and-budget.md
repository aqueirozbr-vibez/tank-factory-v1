# Unit 14: Watchdogs and cost accounting

## Goal

Bound unattended spend and notice hung work: check the task budget and the
installation's daily cap before every agent call, block with `budget` and a
`budget_stop` event when a phase could not fit, and flag a running process
with no event for 15 minutes (PRD §Capability 5 · Budgets, Stuck runs;
TRD §6.3, §6.4). Phase cost and the task and run sums already flow from
unit 13; the proxy spend watchdog arrives with the proxy in phase 3.

## Design

No UI. Visible result: a task whose remaining budget cannot cover the
builder's phase cap blocks before the first call; a day whose spend plus
the cap exceeds the daily cap blocks the same way; a process idle for 16
minutes is flagged once.

## Implementation

### orchestrator/budget/check.py

- `check_budget(writer, task_id, phase_cap_usd, now)` →
  `BudgetDecision(ok, reason, task_spent, task_budget, phase_cap,
  daily_spent, daily_cap)`: refuses when `task_spent + phase_cap >
  task_budget` or `daily_spent + phase_cap > daily_cap`, where daily spend
  is the sum of phase costs started since midnight in the installation's
  time zone.
- `TraceWriter.spent_since(ts)`.

### orchestrator/phases/build.py

- Before each builder call: `check_budget`; on refusal write a
  `budget_stop` event and block with `budget`.

### orchestrator/watchdogs.py

- `find_stuck(db, now, threshold_s)` → running processes whose
  `last_event_at` is older than `STUCK_AFTER_S`.
- `Watchdog(writer, interval_s)` with `tick(now)` that writes one `log`
  event per newly stuck process (`{"stuck": true, ...}`) and remembers what
  it flagged, and `run(stop)` looping on `tick` until the stop event.

## Dependencies

- None new.

## Verify when done

- [ ] A task with budget 1.00 and a builder cap of 3.00 blocks with `budget`
      before any builder call; the `budget_stop` event carries the numbers
- [ ] A day with 52.00 already spent against a 54.00 cap blocks a 3.00
      phase the same way, naming the daily cap
- [ ] The green path still passes when the budget fits
- [ ] A running process idle for 16 min is returned by `find_stuck`; one
      idle for 5 min or already ended is not
- [ ] `Watchdog.tick` flags a stuck process once across two ticks and writes
      a `log` event with `stuck = true`; `run` stops on the event
- [ ] The four quality commands pass
