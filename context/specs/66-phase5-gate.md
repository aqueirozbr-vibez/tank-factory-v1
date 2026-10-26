# Unit 66: Phase 5 gate run

## Goal

The four phase 5 gate conditions, each with its evidence (PRD §Scope ·
Phase 5; TRD §1 phase table, §5.4 budgets, §12 End to end and Load; build
plan): the tech lead completes 10 plan approvals and one monthly audit
cycle from the console with every label under their own login; the
Overview is under 2 s p95 on the pilot's full history; a WCAG 2.2 AA audit
finds 0 critical issues; the kill-switch from the console stops a live task
within 60 s.

## Design

Terminal only. The gate runs on this PC against
`tests/e2e/serve_seeded_api.py`, which became the gate fixture: the perf
seeder's 1,000 tasks, three running tasks (`#7` with its full history and
10,000 rows; `#17` and `#27` with a build) that each gain one `tool_call`
row every two seconds, a `risk:high` plan waiting for approval (`#8`), the
shipped `.factory/` files as two configuration versions, an eval set of 24
items with a baseline run and a blocked `config_pr` run, risk classes on
the merged tasks so the monthly audit queue draws every stratum, a kill
switch without a supervisor, and a webhook door that trusts loopback with a
known secret and admits `factory:ready` labels into `tasks` (a stand-in for
the intake's admission, which also reads the issue on GitHub).

Condition 1 has two halves. The console half runs here: the Approvals and
audit screens render from the queues, the approval card's permission and
CODEOWNER logic, the label route and the audit route are unit-tested
(units 62 and 65), and the audit decision writes its `events` row with the
login against the fixture. The GitHub half, ten `factory:plan-approved`
labels written from the console under the tech lead's own login and read
back by the orchestrator's approval waiter, needs the GitHub App user
authorisation and the scratch repository; the runbook below is the user's.
Condition 2 is measured twice: Playwright loads the Overview ten times
after a warm-up (`e2e/overview.spec.ts`) and k6 drives the Overview's API
reads with three logins for 30 s (`tests/load/overview.js`); the Task
detail with 10,000 rows the same way (`e2e/task-detail.spec.ts`,
`tests/load/task_detail.js`). Condition 3 runs axe-core with the WCAG 2.2
AA tag set on the six screens (eight views, the Approvals and Configuration
tabs included) in light and dark (`e2e/screens.spec.ts`); 0 critical and 0
serious is the pass. Condition 4 is unit 65's check: the kill-switch dialog
flips the flag, cancels the running task and reads back its events row
(87 ms against the fixture; the live-task SIGTERM bound was measured on a
real process tree in the phase 3 gate, spec 46). The load row of TRD §12
adds SSE lag under three running tasks (`tests/load/sse_lag.mjs`, three
streams for 60 s, p95 against the 5 s bound) and admission latency with 50
label events in a minute (`tests/load/admission.js`, signed deliveries,
the task read polled until the row exists).

The keyboard map's row keys were only promised until now: `KeyboardMap`
gained `j`/`k` over `[data-row]` elements and Enter on a focused row opens
its first link or button; the task tables, the audit, questions, findings
and AC tables, the gate ladder and the plan queue mark their rows, and
`console-tokens.css` gives them one focus ring. `e2e/screens.spec.ts`
exercises the whole map.

## What the gate found and fixed

The first pass failed on three fronts, each fixed before the recorded run.
Contrast: the light theme's pass, fail and warn roles sat under 4.5:1 on the
Industry background (#f2f2f3), and the sheet's own ink levels did too (links
and primary buttons on the 3.7:1 accent, table headers at 60% ink,
`.text-muted` at 55%); `console-tokens.css` now darkens the three roles,
adds `--color-accent-strong` (the accent that may carry small text or a
button: the 700 step in light, the base accent in dark) and sets the muted
ink at 70%, all 5.5:1 or better in both themes. Dismissed findings at 60%
opacity put their severity chip and link under contrast in dark; the row is
now muted ink with its chips and links at full colour, a deviation from the
UI context's opacity recorded here. Scrollable evidence, envelope,
instrument, diff and trace regions could not take keyboard focus; they carry
`tabIndex=0` now. Load: the task detail read verified the whole chain span
on every request, which held under one user (p95 1.1 s) but put the k6 p95 at
2.5 s under three; the read now keeps an in-process incremental verification
per task (a verified prefix stays verified; only new rows are recomputed; a
broken chain is never cached; `factory verify` and the export recompute every
row), and the k6 p95 fell to tens of milliseconds.

## Runbook for the tech-lead run (the user, GitHub)

1. Install the console with the GitHub App's user authorisation (spec 57
   runbook: `AUTH_GITHUB_ID`, `AUTH_GITHUB_SECRET`, `AUTH_SECRET`,
   `CONSOLE_SESSION_KEY`) against the scratch repository the phase 2 gate
   uses, with `factory serve --harness fake`.
2. Label ten issues `factory:ready` whose specs the fake planner classifies
   `risk:high` (the seed scripts' billing issues); each stops at the plan
   approval and appears under Approvals · Plans.
3. Signed in as the tech lead, approve each plan from the card; GitHub
   shows `factory:plan-approved` added by the tech lead's login, the
   orchestrator records an `approvals` row bound to the spec sha, and the
   build starts. Evidence: `SELECT task_id, actor_login, bound_sha FROM
   approvals WHERE gate = 'plan'` lists ten rows with that login.
4. After the merges, open Approvals · Audit for the month and decide every
   drawn PR with a reason; evidence: `audit_decisions` rows with the login
   and `kpi_daily.audit_disagreement_*` on the next materialisation.
5. Record the counts in the tracker under the phase 5 gate.

## Runbook for the load checks (this PC)

`uv run python tests/e2e/serve_seeded_api.py <data_dir> 8000`; in
`console/`: `npm run build`, export the e2e environment (spec 59), `npx
next start -p 3100`, `npm run e2e`; then `tests/load/README.md`'s four
commands with `ORCHESTRATOR_URL=http://127.0.0.1:8000`.

## Implementation

- `tests/e2e/serve_seeded_api.py`; `tests/load/{overview,task_detail,
  admission}.js`, `tests/load/sse_lag.mjs`, `tests/load/README.md`.
- `console/e2e/screens.spec.ts` (`@axe-core/playwright`);
  `console/components/KeyboardMap.tsx` (row keys),
  `console/components/keyboard-rows.test.tsx`; `data-row` on the rows of
  `TaskTables`, `AuditTable`, `QuestionsTable`, `FindingsList`, `AcTable`,
  `GateLadder`, `PlanQueue`; the focus ring in `console-tokens.css`.

## Dependencies

- Units 59 to 65; k6 (the Grafana binary) and Node for the load checks.

## Verify when done

- [ ] Overview ≤ 2 s p95 with 1,000 tasks (Playwright and k6); Task detail
      ≤ 2 s p95 with 10,000 rows (Playwright and k6)
- [ ] WCAG 2.2 AA: 0 critical, 0 serious on the six screens in light and
      dark; the keyboard map's keys all work
- [ ] The kill-switch from the console stops the factory and cancels the
      running task within 60 s (unit 65 evidence)
- [ ] SSE lag p95 ≤ 5 s under three running tasks; admission p95 ≤ 2 min
      with 50 label events in a minute
- [ ] User-run: ten plan approvals and one audit cycle under the tech
      lead's own login on the scratch repository
