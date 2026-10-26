# Unit 48: KPI materialiser and audit queue

## Goal

`kpi_daily` carries the five headline metrics and the leading indicators,
materialised nightly and on merge; the monthly audit queue is drawn by
code (every `risk:high` and `risk:governed` merge, a Bernoulli 0.10 draw
of `risk:low`), one decision per task and month; `v_audit_queue` serves
it; no metric is keyed by a login (PRD §Metrics, §Capability 5 · KPIs,
§What is not measured; TRD §4.2 `kpi_daily`, `audit_decisions`, §9).

## Design

Terminal only. `KpiMaterialiser.compute` reads the trace over a 30-day
window (7 days for stuck runs, the current month for audits) and
`materialise` upserts one `kpi_daily` row per metric for the installation's
local day. The headline series follow the PRD metrics table word for
word: `factory_merge_share` (factory merges over every merged PR the
intake observed, the ignored-merge logs included), `merge_rate` (merges
over PRs the factory opened), `unattended_rate` (tasks that reached
`pr_open` or `blocked` with no `engineer` phase before it, over tasks
admitted), `audit_disagreement_low` (rejections over the month's sample,
with the high and governed strata as separate series, never pooled) and
`cost_per_merged_pr` (the sum of `runs.total_cost_usd` over merged tasks,
with `cost_per_merged_pr_median_30d` and `over_budget_count`, budget stops
or spend above budget, beside it). The leading indicators the trace
supports: `repair_rounds_mean` (failed `code_gates` phases per task that
built), `escalation_rate` (blocked over admitted), `review_minutes_median`
(PR open to merge), `eval_pass_rate` overall and per class,
`eval_gap_points` against a set's `reference_pass_rate`,
`protected_path_attempts`, `stuck_runs_7d` and `verdict_override_rate`
(override events over review phases). A ratio with an empty denominator
is stored as `NULL`, never as a fake zero. The audit draw is a
reproducible coin from the task id and the month, so the same month
always draws the same low-risk tasks; a task is drawn once in its life;
the merge observer draws the merged task at once and refreshes the day,
and a nightly loop draws any merge it missed and materialises on every
local day change.

## Implementation

- `orchestrator/trace/kpi.py`: `KpiMaterialiser` (`compute`,
  `materialise`, `sample_task`, `sample_month`, `on_merge`, `run`),
  `Metric`, `ratio`, `audit_sampled`, `local_day`, `local_month`.
- `orchestrator/trace/writer.py`: `upsert_kpi`, `kpis_for_day`,
  `insert_audit_decision`, `decide_audit` (an `audit` event by the tech
  lead, then the row), `audit_queue`, `merged_in_month`.
- `MergeObserver(writer, on_merge=)`; the service wires the materialiser
  and its nightly loop.

## Dependencies

- Units 03 and 34. No new package. The reviewer override rate is one
  series; the per-tier split needs the console's audit findings (phase 5),
  recorded as an open point. Gate-fail distribution stays the
  `v_gate_fail_distribution` view.

## Verify when done

- [ ] Every headline metric and indicator matches a hand-computed fixture
      (two merges of three, three PRs, five admitted, one attended, one
      blocked with two repairs, one over budget, one disagree in the low
      stratum, an eval run at 75 against a reference of 80)
- [ ] The sampler is total on high and governed, reproducible, and draws
      about 10% of low-risk tasks; a task is drawn once
- [ ] The nightly loop materialises at start and on the day change; a merge
      refreshes the day; `kpi_daily` upserts
- [ ] The four quality commands pass
