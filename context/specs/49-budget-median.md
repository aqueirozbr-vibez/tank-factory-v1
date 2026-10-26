# Unit 49: Budget switch to the rolling median

## Goal

After the first week of measurement the per-task budget is 2× the median
cost per task over the trailing 30 days (or over all tasks so far while
fewer than 30 days exist), recomputed daily, with `budget_mode='median'`;
the daily cap follows by trigger (PRD §Capability 5 · Budgets,
§Non-functional · Cost control; TRD §4.2 `installation.budget_mode`,
§6.3).

## Design

Terminal only. `MedianBudget.refresh(now)` reads the installation row:
inside `BUDGET_INSTALL_PERIOD_DAYS` (7) of `installation.created_at` it
returns `install_period` and writes nothing; afterwards it takes the spend
of every task admitted in the trailing `BUDGET_MEDIAN_WINDOW_DAYS` (30)
whose run is over (`pr_open`, `merged`, `blocked`, `cancelled`) and that
spent anything, so a running task's partial spend and an untouched
cancellation are not measurements. With no measurement the install value
stays and the mode stays `install`. Otherwise the budget becomes
`MEDIAN_BUDGET_FACTOR` (2) × the median, rounded to four decimals, through
`writer.set_per_task_budget(mode="median")`; the `installation_daily_cap`
trigger recomputes the daily cap; one `control` event (`budget_median`,
old, new, median, task count, window, previous mode) records the move. A
refresh that lands on the same value writes nothing. `MedianBudget.run`
refreshes at start and on every local day change; the service runs it
beside the KPI loop. Tasks admitted after the switch take the new value
through the existing admission path. The PRD names no floor and no
ceiling, so none is applied; the risk of a small sample setting a budget
below a phase cap is an open question in the tracker.

## Implementation

- `orchestrator/budget/median.py`: `MedianBudget` (`refresh`, `run`),
  `MedianOutcome`, `measured_costs`, `MEASURED_STATUSES`.
- `orchestrator/budget/__init__.py` exports; `orchestrator/service.py`
  wires the loop.

## Dependencies

- Units 41 and 48; constants `BUDGET_INSTALL_PERIOD_DAYS`,
  `BUDGET_MEDIAN_WINDOW_DAYS`, `MEDIAN_BUDGET_FACTOR` (unit 01). No new
  package.

## Verify when done

- [ ] A clock-controlled test keeps the install value on day 6, switches on
      day 8 to 2× the median of the measured tasks (daily cap by trigger),
      writes nothing on a same-day refresh, recomputes on day 9 and drops
      tasks older than 30 days
- [ ] No measurement keeps the install value and mode
- [ ] The daily loop refreshes at start and on the day change
- [ ] The four quality commands pass
