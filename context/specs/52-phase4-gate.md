# Unit 52: Second installation and phase 4 gate run

## Goal

The three phase 4 gate conditions, each with evidence (PRD §Scope · Phase
4; TRD §1 phase table): eval pass rate ≥ 60% overall on the pilot,
reported per class; cost per merged PR ≤ 2× the rolling median over two
weeks; a second installation passes its own 20-issue eval within 10 points
of the pilot's pass rate. The operator-entered `reference_pass_rate` (TRD
§4.2 decision) is how the second installation's gap is measured.

## Design

Terminal only. `factory eval add --set NAME --repo R --items FILE.json
[--reference-pass-rate X]` records a set of closed issues with their
classes (and merged PRs) from a JSON list, idempotently, and stores or
updates the reference pass rate; `factory eval run --reference-pass-rate
X` sets it on the fixture set or an existing set before the run. The eval
report now carries `reference_pass_rate` and `gap_points` (pass rate minus
reference) read from the set, printed as "gap +N points against the
reference (within 10)" or "(wider than 10)"; the gap never blocks a run
(PRD: the gap gates the client pilot agreement, not the change).

`tests/integration/test_phase4_gate.py` records the three conditions
offline: condition 1 replays the 24-issue fixture set on the pilot scratch
repository with the fake harness and reports the overall and per-class
rates; condition 2 runs three issues label → PR → merge through the
composition root and reads `cost_per_merged_pr` against
`cost_per_merged_pr_median_30d` from the KPI materialiser (the two-week
window is the pilot's clock); condition 3 seeds a second scratch
repository, runs `factory install` against it with the fake harness into
its own `etc` and `data` directories (dry run, env files, compose and
systemd through a recording command runner), records that installation's
set with the pilot's rate as the reference, replays it and reports the
gap. Each test prints one `GATE-EVIDENCE` line; the tracker carries the
numbers. The paid second installation on a real repository is the user's;
the runbook below names it.

## Runbook for the second installation (the user, paid)

1. Pick the third, smaller in-house repository (TRD §11 decision) and
   install the factory on its own VM: `factory install --repo-clone
   /var/lib/factory/repo --org <org> --repo <repo2> --issues a b c ...`.
2. Draw its eval set from the last 90 days of merged PRs in the fixed
   class mix (20 to 50 closed issues: bugs, tests, docs, small features)
   into `eval-set.json` as `[{"issue": N, "class": "bug", "merged_pr":
   P}, ...]`, then `factory eval add --set <repo2>-90d --repo <repo2>
   --items eval-set.json --reference-pass-rate <pilot overall>` where the
   pilot overall is the pilot's latest `factory eval run` pass rate.
3. `factory eval run --repo-clone /var/lib/factory/repo --org <org>
   --repo <repo2> --set <repo2>-90d --trigger manual`; the report's gap
   line is the gate: within 10 points passes.
4. On the pilot, after two weeks of merges: `kpi_daily` rows
   `cost_per_merged_pr` and `cost_per_merged_pr_median_30d` for the day;
   the first at most twice the second passes condition 2; `eval_pass_rate`
   ≥ 60 with the per-class rows passes condition 1.

## Dependencies

- Units 47, 49, 51. No new package.

## Verify when done

- [ ] `factory eval add` records a set from JSON idempotently, rejects an
      unknown class, and stores the reference; the report prints the gap
- [ ] Offline: fixture pass rate ≥ 60 overall with every class reported
- [ ] Offline: three merges; cost per merged PR ≤ 2× the median
- [ ] Offline: the second installation installs with the fake harness,
      replays its set and lands within 10 points of the pilot
- [ ] The four quality commands pass; the tracker records the evidence
