# Unit 63: Eval and versions

## Goal

`EvalSetSummary`, `RunsByVersion` with `EvalComparisonTable`, `BlockedChange`,
`InstallDryRun`, `RunEvalButton` under `/eval` (PRD §Capability 5 eval set and
cost control, §Capability 6; TRD §5.2 Eval and versions, §6.1 `/eval/sets`,
`/eval/runs`, `/config/history`, `POST /eval/runs`; ui-context.md · Component
Library · EvalComparisonTable; Layout Patterns · Eval and versions).

## Design

Terminal only. `lib/eval.ts` reads the eval sets, every run, the
configuration history and the installation in parallel, each failure named,
and derives what the screen shows: runs grouped by factory version and
configuration (`0.1.0 · cfg2bbb`, the current configuration marked from the
history), a run's result for the change that triggered it (`blocked` when
the orchestrator marked a drop over 10 points, else `passed`, `running`,
`failed`), the per-class regressions of a blocked run against its baseline
(worst first) and the UI context's result line "Overall 63 → 50 (−13 pt).
bugs 67 → 44.", the latest complete run per set and its gap to the set's
reference pass rate (within 10 points of the pilot or not).

The page (a server component) renders `EvalSetSummary` (per set: repository,
items, the latest complete run's pass rate overall and per class, the gap to
the reference with a pass or fail chip), `RunEvalButton` (client: choose a
set, post `{set_id, trigger: "manual"}` to `/api/console/eval/runs`, which
proxies `POST /eval/runs` with the actor and an Idempotency-Key; disabled
without `eval_run` permission or under 20 items with the reason named; the
orchestrator's 403, 409 run in progress and 422 pass through),
`RunsByVersion` (one `EvalComparisonTable` per version: Version, Trigger,
Overall, bugs, tests, docs, small features, Δ overall with its sign coloured
by role, Result; a blocked run reads "blocked"), `BlockedChange` (each
blocked run with its trigger and reference, the result line and the
per-class regressions), and `InstallDryRun` in the rail: the factory version,
configuration, budget mode (install value from the 3-issue dry run, or the
trailing median from day 8), the per-task budget and the daily cap as N × 3
× budget. The dry run's own derivation record (the three issues, their
costs, the factor) lives in the install log on the data volume
(`install.log`, `orchestrator/budget/dry_run.py`), which no endpoint in the
TRD exposes; the panel says so rather than inventing a read, and the gap is
recorded in the tracker's open questions.

## Implementation

- `console/lib/eval.ts`; `console/components/{EvalSetSummary,
  EvalComparisonTable,RunsByVersion,BlockedChange,InstallDryRun,
  RunEvalButton}.tsx` with CSS Modules; `console/components/eval.test.tsx`.
- `console/app/eval/page.tsx` + `page.module.css`;
  `console/app/api/console/eval/runs/route.ts`.

## Dependencies

- Units 47 (eval runner), 53 (reads), 55 (`POST /eval/runs`), 58, 59, 62
  (`postJson`, the BFF).

## Verify when done

- [ ] vitest: a blocked run reads "blocked" with its delta in the fail role;
      the result line; the regressions; the grouping by version; the gap to
      the pilot; the button's disabled reasons; axe clean
- [ ] `npm run lint`, `typecheck`, `test`, `build` pass
