# Unit 32: Phase 2 gate run

## Goal

Prove the PRD's phase 2 gate: 10 `risk:low` issues go from `factory:ready`
to a pull request with 0 human events, at least 7 merge, one deliberately
ambiguous issue stops with exactly 1 question, and both instruments are
published in the repository (PRD §Scope · Phase 2). Offline first with the
fake harness through the real composition root; then the same loop on the
scratch GitHub repository with the fake harness (plumbing, no model cost);
then the documented real run on the pilot repository, which the user
starts and records.

## Design

Terminal only. Visible result: `tests/integration/test_phase2_gate.py`
green; `factory serve` running against a GitHub repository; PRs opened by
the App; the ambiguous issue's single question comment.

## Implementation

### orchestrator/service.py

- `FactoryService`: the one process. Builds `PlanApprovals`,
  `TaskRunner`, `Supervisor`, `Scheduler`, `Admission`, `Cancellation`,
  `Resume`, `MergeObserver`, the `Router` with every handler, the FastAPI
  app over one inbox, and an optional `Poller`. `start()` runs the
  restart sweep; `run(stop)` dispatches and drives the router and poller;
  `drain()` (tests) processes the inbox and waits for live tasks.
- `build_service(...)`: production wiring from settings: trace, `.factory`
  config from the clone, the installation row, `GitHubClient` with
  `InstallationTokenMinter` and the event recorder, hook IP ranges from
  `GET /meta`, harnesses per role (`roster` or `fake` from a scripts
  directory), the holdout checkout, the App token for pushes.

### orchestrator/harness/fake.py

- `KeyedFakeHarness`: scripts chosen by role and the issue number read from
  the prompt (`specs/<n>-` or the spec title), so one process serves many
  tasks; `from_seed_dir` reads `scripts/<n>.json` (builder),
  `scripts/planner/<n>.json`, `scripts/reviewer/<n>.json`.

### cli

- `factory serve --repo-clone PATH --org ORG --repo REPO [--harness
  roster|fake] [--scripts-dir] [--budget] [--data-dir] [--bind] [--port]
  [--no-poller]`; App credentials from `FACTORY_GITHUB_APP_ID`,
  `FACTORY_GITHUB_INSTALLATION_ID`, `FACTORY_GITHUB_PRIVATE_KEY_FILE`.
- `factory dev issues --repo-clone PATH --org ORG --repo REPO`: opens the
  seed's 11 issues (10 plus the ambiguous one) under the App, in order.
- `factory dev seed` also writes planner and reviewer scripts and the
  ambiguous task 11.

### tests/integration/test_phase2_gate.py

- Seeds a repository with a bare remote; builds `FactoryService` with a
  fake GitHub, the keyed fake harness and the real quality tools; labels 11
  issues; asserts 10 `pr_open` with no comment on their issues, 8 merged
  after merge events, issue 11 blocked `open_questions` with exactly one
  comment carrying the question, no human-actor event, instruments present,
  chain verified.

## Runbook for the user (not run here)

1. GitHub App on the organisation: permissions Contents, Issues, Pull
   requests (read and write), Metadata (read); webhook optional (the 60 s
   poller suffices for the gate); install it on the scratch repository;
   note the App id and installation id; download the private key.
2. Scratch repository: `uv run factory dev seed C:\tmp\scratch`, add the
   GitHub remote, push `main`; create the labels `factory:ready`,
   `factory:plan-review`, `factory:plan-approved`, `factory:needs-human`,
   `factory:budget`, `factory:review`, `risk:low`, `risk:high`,
   `risk:governed`.
3. `.env`: `FACTORY_GITHUB_APP_ID`, `FACTORY_GITHUB_INSTALLATION_ID`,
   `FACTORY_GITHUB_PRIVATE_KEY_FILE`, `FACTORY_DATA_DIR`.
4. `uv run factory dev issues --repo-clone C:\tmp\scratch --org <org>
   --repo scratch` on the fresh repository, so the issue numbers are 1 to
   11 and match the scripts.
5. `uv run factory serve --repo-clone C:\tmp\scratch --org <org> --repo
   scratch --harness fake --scripts-dir C:\tmp\scratch\.factory-dev\scripts`
   and add `factory:ready` to issues 1 to 11 as a write-access user.
6. Expect 10 PRs labelled `factory:review` and one question on issue 11;
   merge at least 7; the merge events close the tasks.
7. The real run: the pilot repository, `--harness roster`, keys in `.env`,
   10 real `risk:low` issues; record cost and merge count in the tracker.

## Dependencies

- None new. The real run needs the user's GitHub App, scratch repository
  and vendor keys.

## Verify when done

- [ ] Offline gate: 10 `pr_open`, 8 merged, issue 11 blocked with one
      question comment, 0 human-actor events, instruments present
- [ ] `KeyedFakeHarness` picks by role and issue and falls back to the
      role default; the seed writes planner and reviewer scripts
- [ ] `factory serve --help` and `factory dev issues --help` parse; the
      service builds from a clone with the fake harness without network
- [ ] The four quality commands pass
