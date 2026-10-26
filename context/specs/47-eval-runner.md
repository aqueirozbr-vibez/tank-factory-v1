# Unit 47: Eval runner

## Goal

A set of at least 20 closed issues in a fixed class mix is replayed on a
scratch branch; the pass rate is reported overall and per class; a drop of
more than 10 points against the baseline marks the run blocked; every
trigger the TRD names is recorded; the 24-issue fixture set runs with the
fake harness; `factory eval run` (PRD §Capability 5 · Eval set,
§Metrics; TRD §4.2 `eval_*` tables, §11 golden sets).

## Design

Terminal only. `EvalRunner.run(set_id, trigger, trigger_ref, baseline)`
opens a scratch trace under `data_dir/eval/<run_id>/` (its own
installation row copied from the installation's) so the replayed tasks
and phases never touch the installation's tasks or KPIs; the installation's
trace keeps `eval_runs` and `eval_results`. Each item runs the real
pipeline, plan → build → review → full suite with the holdout, on a
worktree of `eval/scratch` cut from the base branch, with no GitHub writes
and no approval wait, and the branch is deleted after the item. An item
passes when the review accepts and the full suite passes; cost comes from
the scratch run's total. The baseline is the run named or the latest
complete run of the set; the delta in points decides `blocked` at
`EVAL_BLOCK_DROP_POINTS`; a set under `EVAL_MIN_ITEMS` is refused. The
fixture set lives in `fixtures/eval/set.json`: 24 issues (101 to 124)
drawn from the ten seed tasks in the mix 8 feature, 8 bug, 4 test, 4
docs; the CLI expands each item to its seed task's issue body and scripts.

## Implementation

- `orchestrator/eval/runner.py`: `EvalRunner`, `EvalItem`, `ItemResult`,
  `EvalReport`, `pass_rates`, `delta_points`, `ensure_eval_set`,
  `EvalError`.
- `orchestrator/trace/writer.py`: `create_eval_set`, `find_eval_set`,
  `add_eval_item`, `eval_items`, `start_eval_run`, `record_eval_result`,
  `finish_eval_run`, `get_eval_run`, `latest_complete_eval_run`,
  `eval_results`.
- `orchestrator/workspace/worktree.py`: `create_worktree(branch=)`.
- `cli/eval_cmd.py`: `factory eval run --fixture | --set NAME`
  (`--trigger`, `--trigger-ref`, `--baseline`, `--repo-clone`, `--org`,
  `--repo`, `--data-dir`, `--harness`), the fixture loader and harness.
- `fixtures/eval/set.json`.

## Dependencies

- Unit 45. No new package. Container-mode eval (phases inside task
  containers) is not wired; the runner replays in worktree mode on the
  host, which is what the fixture and the pilot's offline gate need.

## Verify when done

- [ ] The fixture set has 24 distinct issues in the 8/8/4/4 mix
- [ ] The fixture runs with the fake harness: 24 of 24 pass, per-class
      rates recorded, no `eval/scratch` branch left, no task in the
      installation's trace, 24 tasks and 24 full-suite rows in the run's
      scratch trace
- [ ] A second run with four seeded failures scores 83.3, delta −16.7
      against the baseline, `blocked = 1`, trigger and ref recorded
- [ ] A set under 20 items, an unknown class and a missing baseline are
      refused
- [ ] The four quality commands pass
