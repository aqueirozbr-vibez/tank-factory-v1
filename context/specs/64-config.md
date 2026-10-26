# Unit 64: Configuration and change PRs

## Goal

`OpenChanges` with `ConfigChangeCard`, `RosterTable`, `InstrumentTables`,
`BudgetsTable`, `ProtectedPathsTable`, `InstallationPanel`,
`ProposeChangeDialog` opening a `.factory/` PR on branch
`factory-config/<login>/<ts>` under the user's token; the `/changes` stream
invalidating `config:` and `eval:` tags (PRD §Capability 6 configuration;
TRD §5.2 `/config?tab=…` and cache keying, §6.1 `/config`, `/config/history`,
`GET /changes`, BFF `POST /api/console/config-pr`; ui-context.md · Component
Library · ConfigChangeCard; Layout Patterns · Configuration).

## Design

Terminal only. `/config?tab=roster|instruments|budgets|paths|installation`
is one page with URL tabs. `lib/config.ts` reads the loaded configuration
version, its history, the installation and the eval runs; the configuration
and eval reads are tagged (`config`, `eval`) and cached, and `lib/changes.ts`
(started once per process from `instrumentation.ts`) listens to the
orchestrator's `/changes` stream with the service token and calls
`revalidateTag` on `config_loaded` (`config`, `config:<sha>`) and
`eval_complete` (`eval`, `eval:<run_id>`), reconnecting with a backoff;
`lib/api.ts` now caches a tagged read (`force-cache`) and keeps live reads
on `no-store`. The roster rows, the quality commands, the protected paths
(the PRD set the installer applies marked apart from `.factory/factory.yaml`
additions) and the installation facts come from the same read.

`OpenChanges` lists the open pull requests on `factory-config/*` branches
under the signed-in user's token (cached 60 s per repository) as
`ConfigChangeCard`s: the file, the PR link, the unified diff lines, the eval
line matched to the run the orchestrator ran for that PR (`trigger
config_pr`, the PR number as its reference): "Eval running", the UI
context's result line "Overall 63 → 50 (−13 pt). bugs 67 → 44." with "Eval
passed" or, in the error role, "Eval blocked the change.", and the footer
"Opened by @login · reason · not applied until merged" (the reason is the
first line of the PR body the console wrote).

`ProposeChangeDialog` (client, Radix Dialog, `Esc` cancels) takes the file
(one of `.factory/roster.yaml`, `quality.yaml`, `risk.yaml`,
`review-policy.yaml`, `factory.yaml`), the exact text to find at main and
its replacement, and a Reason, showing the file as it is at main (read under
the user's token, cached 60 s). `POST /api/console/config-pr` follows the
BFF order with action `config_pr`, refuses with 429 past 10 a day per
login, reads the file at main, applies the edits (each find must occur
exactly once and the result must differ, else 422 `edit_rejected`), creates
`factory-config/<login>/<ts>` from main (422 `branch_exists` when GitHub
already has it), commits the file on the branch and opens the PR under the
user's token with the reason as the body's first line and the note that
nothing applies before merge; it answers `{pr_url, branch, number}`. The
orchestrator loads the change only from the push to main as a new
configuration version, after the eval set has run.

The instruments tab notes the tech lead's class: instrument changes are
proposed the same way, gated on the eval set and reviewed by a CODEOWNER.
The build plan's check that a proposed change appears as a PR under the test
user with its eval result on the card needs the GitHub App authorisation and
the scratch repository (user items); the derivations and the cards are
vitest here.

## Implementation

- `console/lib/{config,changes}.ts`; `console/lib/{api,github}.ts`
  extended; `console/instrumentation.ts`; tests `lib/changes.test.ts`,
  `components/config.test.tsx`.
- `console/components/{OpenChanges (ConfigChangeCard),ConfigTables
  (RosterTable, InstrumentTables, BudgetsTable, ProtectedPathsTable,
  InstallationPanel),ProposeChangeDialog}.tsx` with CSS Modules.
- `console/app/config/page.tsx` + `page.module.css`;
  `console/app/api/console/config-pr/route.ts`.

## Dependencies

- Units 53 (`/config`, `/changes`), 57, 62 (BFF, GitHub helpers, limits), 63
  (eval result lines).

## Verify when done

- [ ] vitest: the edit rule (exactly once, something changes); the branch
      name and PR body; the eval match by PR number; the blocked card's
      error-role line and diff; the roster and protected-path tables; the
      disabled propose button; the SSE parser and tag mapping; the tagged
      cache init; axe clean
- [ ] `npm run lint`, `typecheck`, `test`, `build` pass
- [ ] User-run (GitHub App authorisation, scratch repository): a proposed
      change appears as a PR under the test user and its eval result shows
      on the card; nothing is applied before merge
