# Unit 59: Overview

## Goal

`KpiRow`, `NeedsHumanTable`, `RunningQueuedTable`, `SpendByTask`,
`IndicatorsPanel`, `StuckFlag`, `NavBadge` (30 s poll); the kill-switch pill
(its dialog is unit 65); Playwright against 1,000 seeded tasks under 2 s
p95; the Indicators panel collapsed by default (PRD §Capability 6; TRD
§5.1, §5.2 Overview, §9; ui-context.md · Layout Patterns · Overview).

## Design

Terminal only. `lib/overview.ts` makes the Overview's reads in one place
and in parallel (installation, KPIs, indicators, the plan queue, blocked,
running and queued tasks, the needs-human count), each failure named in a
status line instead of a blank screen; it also carries the five headline
tiles in the PRD's order with their targets (merge share ≥ 30%, merge rate
≥ 70%, unattended ≥ 80%, audit disagreement ≤ 5%, cost per merged PR at
most twice the 30-day median with zero over budget) and turns a `kpi_daily`
row into a tile status. The page (a server component) renders `KpiRow`,
then two columns: the main one with `NeedsHumanTable` (plans waiting from
the approval queue plus blocked tasks, each linking to where the human
acts) and `RunningQueuedTable` ("Queued, n ahead" from the queue order);
the rail with the "Spend today" `Meter` against the daily cap, `StuckFlag`
and `SpendByTask`; and `IndicatorsPanel` at the bottom, a `<details>`
collapsed by default whose state is remembered per browser. `NavBadge`
in the shell is a client island that polls the console's own read proxy
`/api/console/needs-human` every 30 s behind the session. The shell's login
pill now shows the session's login and permission, or a sign-in link. The
"Spend today" input is new on the installation read: `spent_today_usd`
(today's phase spend since the installation's midnight), the TRD's
Overview meter needs it and the endpoint table did not name a source, so
it is recorded here rather than invented silently.

Playwright (`console/e2e/overview.spec.ts`) runs against an orchestrator
API served over a trace seeded with 1,000 tasks by
`tests/e2e/serve_seeded_api.py` (the perf test's seeder, plus the running
task unit 60 needs). The run signs in without GitHub: `e2e/global-setup.ts`
creates a `console.db` session row the way sign-in does and mints the
Auth.js cookie that names it, so it needs the same `AUTH_SECRET`,
`CONSOLE_SESSION_KEY` and `CONSOLE_DB_PATH` the console under test was
started with (nothing touches GitHub; the minted state is gitignored under
`e2e/.auth/`). One untimed request warms the server, then ten timed loads
give the p95 (a steady-state figure); the test also asserts the headline
tiles, the tables, the meter and the collapsed panel. Runbook:
`uv run python tests/e2e/serve_seeded_api.py <data_dir> 8000`; in
`console/`, `npm run build`, then with `ORCHESTRATOR_URL`, `AUTH_SECRET`,
`CONSOLE_SESSION_KEY`, `CONSOLE_DB_PATH` and `CONSOLE_URL` exported,
`npx next start -p 3100` and `npm run e2e`.

## Implementation

- `console/lib/overview.ts`, `console/components/{KpiRow,TaskTables,
  IndicatorsPanel,StuckFlag,NavBadge}.tsx` with CSS Modules,
  `console/components/Shell.tsx` (badge island, login pill),
  `console/app/page.tsx` + `page.module.css`,
  `console/app/api/console/needs-human/route.ts`.
- `orchestrator/models/api.py` `InstallationView.spent_today_usd`,
  `api/read.py`; `openapi.json` and `lib/api.types.ts` regenerated.
- `console/playwright.config.ts`, `console/e2e/global-setup.ts`,
  `console/e2e/overview.spec.ts`, `tests/e2e/serve_seeded_api.py`,
  `@playwright/test` (chromium); `npm run e2e`.

## Dependencies

- Units 58 and 53 (and 57 for the session-gated proxy).

## Verify when done

- [ ] Playwright against 1,000 seeded tasks: Overview under 2 s p95; the
      Indicators panel collapsed by default; tiles, tables and the meter
      render
- [ ] vitest: tile statuses and the badge total; `npm run lint`,
      `typecheck`, `build` pass; the Python checks pass with the new field
