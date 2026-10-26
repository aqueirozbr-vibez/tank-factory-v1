# Progress Tracker

Update this file after every meaningful implementation change.

## Current Phase

- PRD Phase 1, Build loop: all 16 units built; the gate passes with the fake
  harness. The real Claude Code run (paid) is pending the user's go-ahead.
- PRD Phase 2, Spec and review (units 17 to 32): done offline; the GitHub run is the user's.
- PRD Phase 3, Delivery and isolation (units 33 to 46): done; the gate
  passes offline and on Docker Desktop (unit 46 evidence below); the
  pilot-VM run with the real CLIs is the user's.
- PRD Phase 4, Eval and KPIs (units 47 to 52): done; the gate passes
  offline (unit 52 evidence below); the paid pilot fortnight and the real
  second installation are the user's; the budget derivation is an open
  question for the user (Open Questions).
- PRD Phase 5, Console (units 53 to 66): done; the gate passes offline on
  this PC (unit 66 evidence below: Overview and Task detail p95, WCAG 2.2 AA
  with 0 critical and 0 serious in light and dark, the kill-switch from the
  console); the GitHub half of the first condition (10 plan approvals and
  one audit cycle under the tech lead's own login) is the user's run.

## Current Goal

- All 66 units are built; unit 67 (an amendment) records the user's budget
  decisions of 2026-10-25. What remains is the user's: the paid and
  GitHub-side gate runs (Next Up).

## Completed

- 2026-10-24 — Context files (`context/*.md`), `CLAUDE.md`, build plan
  (`context/specs/00-build-plan.md`, 66 units).
- 2026-10-24 — Unit 01, repository scaffold: git repository on `main`, `uv`
  project with `orchestrator` and `cli`, ruff, mypy strict, pytest, PRD
  constants, `Settings`, error base, `factory --version`. Commit `ad329a9`.
- 2026-10-24 — Unit 02, trace schema and single writer:
  `migrations/0001_init.sql` (TRD §4.2), `TraceDB` (WAL, pragmas, forward-only
  runner, transaction lock), `TraceWriter` (installation, config versions,
  tasks, runs, phases, gate results, envelopes, processes), schema enums,
  `clock.py`, `ids.py`. 46 tests green.
- 2026-10-24 — Unit 03, events hash chain: RFC 8785 canonicaliser
  (`trace/canonical.py`, verified against the RFC vector and ES6 number
  cases), `trace/chain.py` (genesis, `append_event`, `verify_chain`,
  `null_payloads`), migration `0002_installation_install_id`,
  `TraceWriter.write_event`, Hypothesis property tests. 77 tests green.
  Hypothesis found and I fixed a non-idempotent case: integer-valued doubles
  above 2^53 now re-parse as integers that are accepted when exactly
  representable.
- 2026-10-24 — Unit 04, factory configuration: `models/config.py`
  (`RoleConfig`, `Roster` with the PRD rules, `QualityCommands`,
  `FactorySettings`, `FactoryConfig`), `config/loader.py` (YAML, tree sha,
  protected set = PRD five + lockfiles + additions), the shipped default
  `.factory/` in `fixtures/factory_config/` including `risk.yaml` and
  `review-policy.yaml` as data. 89 tests green.
- 2026-10-24 — Unit 05, harness adapter and fake: `harness/base.py`
  (contract), `harness/process.py` (new session or process group,
  `terminate_tree` with grace then force on both platforms,
  `run_with_timeout` streaming stdout and capping stderr),
  `harness/sink.py` (tool calls to `tool_call` events),
  `harness/fake.py` (scripted replay with file effects and a real sleeping
  subprocess for timeouts), `Settings.subprocess_base_env()` allow-list,
  `fixtures/envelopes/builder_green.json`. Tree-kill verified on Windows.
- 2026-10-24 — Unit 06, Claude Code adapter: `harness/claude_code.py`
  (exact argv for `claude -p --bare --output-format stream-json --verbose
  --json-schema ... --permission-mode dontAsk --permission-prompts none`,
  prompt on stdin; `StreamParser` pairing tool_use with tool_result; result
  mapping for success, the four error subtypes, SIGTERM 143 and timeout;
  version from the binary). Flags verified against the CLI reference,
  headless docs and the bundled binary 2.1.309 (offline `--version` test).
  Stream fixtures are hand-authored to the documented shapes.
- 2026-10-24 — Unit 07, envelopes and schema gate: `models/envelopes.py`
  (`PlanOutput`, `BuildOutput`, `ReviewOutput`, `Finding`, `VerifyOutput`,
  closed schemas, relative-path and spec-artifact rules), `gates/base.py`
  (`Check`, `Violation`, `GateResult`, `GateContext`, `write_gate` writing
  the row and the `gate_pass`/`gate_fail` event), `gates/envelope_schema.py`
  (`envelope_schema_gate`, `record_envelope`). 124 tests green.
- 2026-10-24 — Unit 08, permissions gate: `workspace/git.py` (explicit git
  subprocess, ls-files, hash-object, checkout), `gates/permissions.py`
  (snapshot of tracked and untracked files, diff, classification into
  protected_path / dependency_change / outside_boundary / test_location,
  rollback of violating paths, `record_permissions` with the incident
  event). Seeded holdout edit rolled back 10 of 10 in tests.
- 2026-10-24 — Unit 09, claim gates: `gates/claims.py` (`artifacts_exist`,
  `files_non_empty`, `diff_matches_claims`, `claim_gates` with
  `first_failure`); unclaimed changes recorded as informational checks.
- 2026-10-24 — Unit 10, quality runner and self-test: `gates/quality.py`
  (one subprocess per gate in PRD order, short-circuit, stdout and stderr
  tails capped at 4,000, timeout and missing-binary failures,
  `run_full_suite`), `gates/selftest.py` (every command must be found and
  fail in `fixtures/known_failing/`; `assert_self_test` refuses start-up),
  the fixture itself, and an integration test proving the shipped
  `quality.yaml` commands all fail inside it. 153 tests green.
- 2026-10-24 — Unit 11, worktree workspace: `workspace/worktree.py`
  (`agent/<issue>` from the default branch or `origin/HEAD`, idempotent
  create, reuse of an existing branch on a new attempt, `commit_all` under
  the factory identity, `diff_against_base`, `changed_files_since`,
  `blob_sha`, `remove_worktree` keeping the branch); settings for the git
  author.
- 2026-10-24 — Unit 12, builder prompt: `prompts/builder/system.md` and
  `user.md` (byte-stable prefix order), `prompt_builder.py` (`load_guide`
  with `.factory/prompts/` overrides and a guide sha, `build_builder_prompt`
  over the allow-list, `forbid_secrets` refusing credential shapes and
  non-text inputs), `PromptError`; prompts force-included in the wheel.
- 2026-10-24 — Unit 13, build phase state machine: `phases/build.py`
  (`BuildPhase`: one `build` row per builder call, one `code_gates` row per
  round, `commit` row; envelope re-prompt once; permissions block with
  rollback and incident; claims then quality; `REPAIR_CAP` same-session
  repairs with a `VerifyOutput`; infra retries with 5/20/60 s backoff that
  do not consume repairs; budget, max-turns, timeout and killed mapped to
  blocked reasons), `issue_to_pr.py` (`run_build_slice`),
  `harness/registry.py`, `Settings.agent_env()` (phase 1 pass-through),
  `fixtures/specs/418-invoice-due-date.md`.
- 2026-10-24 — Unit 14, budget and watchdogs: `budget/check.py`
  (per-task budget and daily cap in the installation time zone before every
  builder call, `budget_stop` event, blocked `budget`),
  `TraceWriter.spent_since`, `watchdogs.py` (`find_stuck` over
  `processes.last_event_at`, `Watchdog.tick` flags once with a `log` event,
  `run` loop). The `product` repository fixture moved to `tests/conftest.py`.
  `tzdata` added so `zoneinfo` works on Windows.
- 2026-10-24 — Unit 15, phase 1 CLI: `cli/seed.py` (`factory dev seed`:
  scratch repo with the `demo` package, ten seven-section specs, ten fake
  scripts whose effects pass real ruff, mypy strict and pytest, violation
  scripts, `.factory/` with tools from the factory environment),
  `cli/run_spec.py` (self-test, installation, admission, worktree,
  `run_build_slice`, outcome summary, exit codes), `cli/trace_cmd.py`
  (`trace`, `verify`), subcommands in `cli/main.py`. The unit test runs
  the real ruff, mypy and pytest commands inside the scratch repository.
- 2026-10-24 — Unit 16, phase 1 gate run (`tests/integration/
  test_phase1_gate.py`, fake harness, real ruff/mypy/pytest): 10 of 10
  specs green with one factory commit per `agent/<n>` branch, 10 success
  runs, 40 tool-call rows, 0 failed gates, chain verified; 10 of 10 seeded
  `tests/acceptance/` edits rolled back and blocked `protected_path` with 10
  incidents; 20 of 20 self-test refusals of a placeholder command, and the
  seeded configuration passes the self-test. Real-harness run: pending.
- 2026-10-24 — Unit 17, GitHub client: `github/auth.py` (RS256 App JWT,
  `InstallationTokenMinter` per task with refresh margin), `github/client.py`
  (issues, comments, labels, permission, Contents, refs, pulls, reviewers;
  retries on 5xx and rate limits; `label_write`/`comment_write` events with
  `identity = app`), `github/codeowners.py`; recorded fixtures in
  `fixtures/github/`. Dependencies httpx, pyjwt, cryptography.
- 2026-10-24 — Unit 18, FastAPI app and webhook receiver:
  `models/events.py` (`NormalisedEvent`), `intake/webhook.py` (HMAC,
  `GitHubIpRanges` with daily staleness, `normalise` for labeled, unlabeled,
  merged, review, check_run, workflow_run, push; `record_delivery` with the
  `(event_type, github_event_id)` key), `api/app.py` (`/healthz`,
  `/webhooks/github` → 403/401/400/202, inbox queue), writer
  `record_github_event`; fixtures in `fixtures/webhooks/`. Dependencies
  fastapi, uvicorn.
- 2026-10-24 — Unit 19, reconciliation poller: `intake/poller.py`
  (issue events, merged `agent/*` pulls, reviews and check runs for
  `pr_open` tasks, `main` head compare → push; isolated sub-polls with
  `error` events; cursors in `poll_cursors`, migration 0003), client list
  endpoints, writer cursor and PR helpers; fixtures in `fixtures/github/`.
- 2026-10-24 — Unit 20, router, admission, queue and cancel:
  `intake/router.py` (routes by label, merge, review, CI result, config
  push; unhandled → `log`), `intake/admission.py` (actor sensor with the
  5-minute permission cache, bots refused, idempotent task row before the
  worktree, re-admission of cancelled tasks as a new attempt, queue
  comment; `Cancellation` with supervisor cancel, open work closed,
  `control` event, comment naming the last completed phase),
  `intake/scheduler.py` (N slots, oldest queued first, promotion on
  finish), `supervisor.py` (asyncio task per live task, cancel, cancel_all),
  `run_with_timeout` kills the child tree on cancellation; writer helpers.
  Remote `agent/<issue>` branches are pushed at delivery, not at admission
  (worktree mode keeps admission offline).
- 2026-10-24 — Unit 21, issue template, holdout, fetcher:
  `deploy/product-repo/.github/ISSUE_TEMPLATE/factory-task.yml` (issue form
  with the five required fields; the seed installs it),
  `workspace/holdout.py` (`HoldoutCheckout`: clone, refresh, test ids,
  `has_test`, `copy_into`, `assert_outside`), `intake/issue_fetcher.py`
  (`TaggedIssue` with sections from `##`/`###` headings, trusted flags,
  language, missing required sections, holdout ids, escaped data
  rendering), `FactorySettings.holdout_branch`.
- 2026-10-24 — Unit 22, planner and plan phase: `prompts/planner/`
  (guide, user template in TRD §8 order, `spec-template.md` with the gate
  rules), `build_planner_prompt` and `load_prompt_text` in the prompt
  builder, `phases/plan.py` (`PlanPhase`: budget check, `plan` and
  `spec_gate` rows, envelope re-prompt once, `specs/`-only permissions with
  rollback, claim gates with up to two corrections, harness outcomes mapped
  to blocked reasons, `PromptError` blocks `spec_gate`, sets
  `tasks.spec_path`). `PlanPhase` mirrors `BuildPhase`; a shared phase base
  is a refactor to schedule after phase 2.
- 2026-10-24 — Unit 23, spec gate: `gates/spec.py` (`parse_spec`, the four
  gates `spec_sections`, `spec_ears`, `spec_ac_holdout_map`,
  `spec_adr_cited`, `run_spec_gates`), `PlanPhase` runs them after the
  claims with a refreshed holdout, blocks `missing_holdout` at once with a
  comment and `factory:needs-human`, corrects up to twice, and blocks
  `open_questions` with the questions posted in the issue's language;
  fixture and seed specs map each criterion to a holdout id.
- 2026-10-24 — Unit 24, risk classifier: `instruments/risk.py`
  (`RiskInstrument.from_yaml`/`load`, tier letters from the planner's
  `tiers_touched` plus Scope paths matching a tier's `paths` prefixes,
  strongest tier, size/blast/dependency multipliers, governed by tier A,
  U when no Scope path exists or no tier is known, `override`,
  `write_risk_header`); the PRD worked examples reproduce exactly (40
  low, 109.2 high, 30 low, 60 high). `PlanPhase(..., instrument)`
  classifies after the gates, sends U back as a planner correction
  (shared cap), writes the header, `tasks.risk`/`risk_score` and the
  `risk:*` label in a `risk` row, then commits the spec in a `commit`
  row and binds `tasks.spec_sha` to the blob; `TraceWriter.set_task_risk`.
  Decision: `risk.yaml` tiers carry `examples` for humans and the
  planner, not keywords matched by code (PRD worked example 5).
- 2026-10-24 — Unit 25, plan approval waiter: `phases/approval.py`
  (`PlanApprovals.on_label` judges every `factory:plan-approved` event,
  live waiter or not: not awaiting, stale against
  `tasks.spec_committed_at`, no write access, governed without a
  CODEOWNER of `tasks.governed_path`, else an `approvals` row bound to
  `tasks.spec_sha` plus an `approval` event under the login;
  `PlanApprovalPhase` posts the plan-review comment and
  `factory:plan-review`, reuses a covering row on restart, waits for a
  counted verdict on its sha), migration 0004 (`spec_committed_at`,
  `governed_path`), `PhaseRecorder`, `intake/actors.py`,
  `NormalisedEvent.occurred_at` from polled issue events, CODEOWNERS
  read from the clone's default branch. Limitation to decide before the
  phase 2 gate: `@org/team` owners are not resolved (needs the Teams
  API under the App), so a governed approval needs an individual owner
  line.
- 2026-10-24 — Unit 26, Codex adapter: `harness/codex.py`
  (`CodexStreamParser` over the `codex exec --json` events verified in
  `codex-rs/exec/src/exec_events.rs`; `CodexHarness` with exact argv,
  schema file in a scratch directory outside the worktree, `read-only`
  sandbox for the reviewer and `workspace-write` otherwise, system
  prompt folded above the task, `resume <thread> -`), three
  hand-authored streams (`codex_review_{green,failed,cut}.jsonl`),
  `tests/unit/test_harness_contract.py` over `fake`, `claude_code` and
  `codex`, registry and env pass-through (`CODEX_API_KEY`,
  `CODEX_HOME`). The CLI is not installed here: the user's first real
  run must replace the hand-authored streams with recordings. Codex
  has no max-turns or budget flag; the timeout and, from phase 3, the
  proxy cap bound the reviewer.
- 2026-10-24 — Unit 27, reviewer and review phase: `prompts/reviewer/`
  guides, `build_reviewer_prompt` (policy, spec, builder envelope, gate
  evidence, diff; the diff is secret-scanned and a credential blocks
  the task for a human), `gates/verdict.py` (`verdict_consistent`: hard
  violations re-prompt once then block `human_decision_needed`; an
  acceptance criterion without an evidenced verdict re-prompts once then
  forces `revision_needed`), `phases/review.py` (`ReviewPhase` in a
  fresh session with `Read`, `Glob`, `Grep`, own `review` row with
  tokens and cost, same-vendor roster refused, `handoff` event with the
  effective verdict and `missing_ac`), `PhaseRecorder.finish(result=)`,
  `TraceWriter.last_phase_seq`/`gate_results_for_run`/`latest_envelope`,
  `VERDICT_REPROMPT_CAP`.
- 2026-10-24 — Unit 28, verdict scorer and revision loop:
  `instruments/verdict.py` (`ReviewPolicy`, `diff_lines`, spec-link,
  evidence and scope factors, Non-goals dismissal by explicit citation
  or a stem unique to a Non-goals line, security Blocker and reviewer
  `human_decision_needed` classes, `third_revision` from
  `revisions_so_far`, `override_verdict` recorded as an `audit` event
  with reviewer model and policy version); the PRD's five worked
  examples reproduce (56/44, 6/94, 100/0 then human, 0 dismissed,
  override). `issue_to_pr.run_build_and_review` runs build → review →
  revision loop with `REVISION_CAP`, posts the findings and
  `factory:needs-human` on a human class; `BuildPhase` gained a
  `revision` mode (findings section, run sequence continued, unchanged
  tree passes on HEAD). Decision: where the policy prose and the worked
  examples disagree (a finding without a location), the examples win:
  it keeps its tier at the assertion-only multiplier; only a cited line
  outside the diff drops to Unverifiable. `met = true` findings are
  verdicts, not scored.
- 2026-10-24 — Unit 29, comment templates: Jinja2 under
  `orchestrator/templates/comments/` (ten templates), `render_comment`
  refusing undeclared and missing fields, `nomention` filter for
  agent-authored text, `messages` as a thin renderer with unchanged
  texts, new `blocked` (reason, phase, command, exit code, output tail,
  PRD resume rule per reason) and `budget_stop` comments, snapshots under
  `fixtures/comments/`. New runtime dependency: `jinja2`.
- 2026-10-24 — Unit 30, provisional delivery: `workspace/git.py`
  `push_argv`/`push_branch` (token as a transient `http.extraheader`,
  never in the URL or the trace), `templates/pr_body.md.j2`
  (provisional: spec link, risk, head sha, verdict, gate table, phase
  table with harness, model and cost, total), `render_template`,
  `phases/deliver.py` (`DeliverPhase`: push with infra retries then
  blocked `timeout`; reuse `tasks.pr_number`, adopt an existing PR for
  the branch, else create one titled from the commit message;
  `factory:review`; CODEOWNER logins and teams requested as reviewers;
  `pr_open` with number and head sha; `MergeObserver.on_merged` →
  `merged`), `issue_to_pr.deliver`, writer readers `phases_for_run`,
  `task_for_pr`, `task_for_branch`. Tests push to a local bare remote.
- 2026-10-24 — Unit 31, runner, resume and restart: `orchestrator/runner.py`
  (`TaskRunner.run` composes plan → approval → build and review →
  deliver in one run; entry point from the trace: recorded PR or
  accepted verdict → deliver, no spec → plan, human-cause block →
  plan after the cause check (new issue comment; holdout ids present
  after refresh; spec blob changed), else approval then build; the
  Blocked-Comment from the last failed gate row unless the phase
  already notified), `intake/resume.py` (`Resume.on_unlabeled`: write
  access, never-resume reasons, budget needs a `budget_raise` control
  event, else requeue with attempt + 1 and dispatch),
  `watchdogs.sweep_on_start` (orphan processes ended, running phases
  and runs failed, tasks requeued), `issue_to_pr.build_and_review`
  inside a run with per-role deps, `PlanPhase` accepts an identical
  spec rewrite (`replan: spec unchanged`), `RiskBreakdown.recorded`,
  `resume_refused` comment. The unit 31 edge case is closed.
- 2026-10-24 — Unit 32, phase 2 gate (offline): `orchestrator/service.py`
  (`FactoryService` composition root; `build_service` from settings with
  the App token minter, event recorder, hook IP ranges from `GET /meta`,
  harnesses per role or the keyed fake, holdout checkout, poller),
  `KeyedFakeHarness` (scripts by role and issue number from the prompt),
  seed planner and reviewer scripts plus the ambiguous task 11 and issue
  bodies, `factory serve` and `factory dev issues`, GitHub App settings,
  `GitHubClient.get_meta`/`create_issue`,
  `tests/integration/test_phase2_gate.py`: 11 issues through the real
  composition root with the real quality tools: 10 `pr_open` with no
  comment, 8 merged on merge events, issue 11 blocked with exactly one
  question, 0 human-actor events, instruments present, chain verified.
  The GitHub run (scratch repository, then the pilot) is the user's;
  the runbook is in the unit spec.
- 2026-10-24 — Unit 33, PR body from the trace: `templates/pr_body.py`
  (`pr_body_fields` from tasks, phases, gate rows and the run's
  `verdict_policy` event; `render_pr_body`), the full `pr_body.md.j2`
  (spec and risk line, review line, gate table with evidence, phase
  table with tokens, cost and session ids, roles table, advisory and
  dismissed findings with the Non-goals line, the spec in a details
  block), `cell` filter, `TraceWriter.latest_log_payload`; delivery
  renders from the trace; snapshot under `fixtures/pr_body/`.
- 2026-10-24 — Unit 34, CI re-entry: `intake/ci.py` (`CiReentry`:
  check-run and workflow-run failures and `changes_requested` on a
  `pr_open` task's head sha fail the open `ci_wait` row, write a
  `ci_reentry` control and requeue; stale shas and a pending re-entry are
  ignored), the runner enters at build on a pending re-entry with the CI
  failure as the builder's previous verification and the re-entries
  since the last resume counted against `REPAIR_CAP` (the fourth blocks
  `repair_cap`; a resume gives fresh caps), delivery opens a `ci_wait`
  row (kind engineer, owner github) and the merge passes it;
  `BuildPhase(initial_verify, repairs_used)`; writer readers
  `open_phase`, `finish_open_phase`, `latest_control`,
  `latest_handoff_seq`, `count_controls`; `CI_WAIT_TIMEOUT_S`. Limit:
  the human review's comments do not reach the builder in v1, only that
  changes were requested and by whom.
- 2026-10-24 — Unit 35, model proxy and virtual keys: `budget/proxy.py`
  (`ProxyAdmin`: health, create key with `max_budget` and duration, key
  info, delete; `litellm_config(roster)`), `budget/keys.py`
  (`PhaseKeys.issue`/`settle`: the proxy spend becomes the phase cost,
  the key is revoked at phase end, a failed phase at the cap becomes
  `error_max_budget_usd`; `agent_env_for` builds the agent env with
  only the virtual key, the proxy URL, `FACTORY_PHASE_ID` and a
  factory-owned `CODEX_HOME`), the three agent phases issue and settle
  keys and record `virtual_key_id`, `BuildDeps.keys`/`RunnerDeps.keys`,
  `build_service` fails closed when the proxy is configured but down,
  `Settings.proxy_url`/`proxy_master_key`, `factory proxy up|down|status`,
  `deploy/docker-compose.proxy.yml` (LiteLLM + Postgres, loopback 4000)
  and `deploy/litellm/config.yaml` generated from the shipped roster;
  the container test is opt-in (`FACTORY_DOCKER_TESTS=1`).
- 2026-10-24 — Unit 36, task image and container workspace:
  `deploy/task-image/Dockerfile` (python 3.12 slim, uv, Node 22 with the
  Claude Code and Codex CLIs copied from the official images, git,
  ripgrep, tini, user agent 10001, install command baked with
  `UV_PROJECT_ENVIRONMENT=/opt/venv`), `workspace/image.py` (digest over
  Dockerfile, install command, `.factory` tree and lockfiles at `main`;
  `ensure_image` from a clean `git archive`), `workspace/container.py`
  (`DockerCli`, mounts: worktree at /work, clone `.git` read-only, the
  worktree admin dir read-write; `GIT_DIR`/`GIT_WORK_TREE` inside; exec
  with a 0600 env file; timeout kills the container), runner lifecycle
  (create after the worktree, phases through the container runner,
  removed when the run ends), `Settings.workspace|task_image|docker_bin|
  docker_network`, `FactorySettings.install_command`,
  `make_harness(runner=)`, `FakeHarness.with_runner`, admission records
  the kind, `factory image build`; container tests opt-in. Verified on
  Docker Desktop: the LiteLLM compose stack (port 4100; Docker Desktop's
  backend holds 127.0.0.1:4000, hence `FACTORY_PROXY_PORT`) and a real
  task container (exact env, cwd mapping, sleep inside, timeout kill,
  removal).
- 2026-10-24 — Unit 37, egress (user decision: internal network here,
  nftables for the VM): `ensure_network` creates `factory-tasks` with
  `--internal` and refuses an open one; the LiteLLM compose service
  joins it under the alias `proxy`; `Settings.docker_network` defaults
  to it and `proxy_url_in_tasks` (`http://proxy:4000`) is what
  `PhaseKeys` puts in a container's env; `watchdogs.nftables_rules`
  (established and the proxy port accepted, the rest logged with prefix
  `factory-egress ` and dropped), `parse_denial`, `EgressTailer` writing
  `egress_denied` with the task resolved from the source IP,
  `journal_lines` for the VM; `deploy/egress/` README and rendered
  example. Limit on this host: denials are not kernel-logged; unit 38's
  hook denials record attempts as `tool_call` rows.
  Verified on Docker Desktop: from the internal network GitHub and PyPI
  are unreachable while the `proxy` alias answers; the real-container test
  now compares against the image's declared ENV (GPG_KEY, PYTHON_VERSION).
- 2026-10-24 — Unit 38, PreToolUse hook bundle: `deploy/hooks/`
  (`factory_pretooluse.py`, the shipped `settings.json`, `factory.rules`,
  README). Claude Code loads the bundle through `--settings` because
  bare mode skips `.claude/settings.json` auto-discovery; the hook
  denies `git push`, `git commit` (the PRD says the agent never runs
  it), `gh`, `curl`, `wget`, `rm -rf`, and protected-path or
  outside-workspace writes, in wrapped, chained, nested-shell and
  redirected forms, with exit 2 and the reason; permission deny rules
  are a second layer. Codex gets
  `sandbox_workspace_write.network_access=false` and an execpolicy
  rules file installed into `CODEX_HOME` at service start. The
  protected set travels as `FACTORY_PROTECTED_PATHS`; `ToolCall.denied`
  reaches `tool_call.denied` and `hook_denials` on `agent_end`; the fake
  harness runs the real hook for every scripted call (inside the
  container in container mode). The bundle is mounted read-only at
  `/opt/factory/hooks`; `verify_hook` fails closed at service start and
  after each container start; `CODEX_HOME` is rewritten inside
  containers. Limits: the Codex rules file reaches a container only
  with unit 42; the denial's stream shape is hand-authored from the
  hooks reference until the phase 3 gate records it.
  Verified on Docker Desktop: inside a real task container the mounted
  hook denies `git push`, allows `git status`, and the mount is read-only;
  the integration suite passes through the composition root.
- 2026-10-24 — Unit 39, full suite with the holdout:
  `orchestrator/phases/full_suite.py` (`FullSuitePhase`,
  `FullSuiteOutcome`, `failing_tests`, `holdout_copied`,
  `repair_request`). The runner runs it after an accepted review and on
  every entry at the deliver point, so the suite always ran on the SHA
  that gets pushed. Worktree mode copies `tests/acceptance` in for the
  phase and restores the tree with `git checkout` + `git clean`;
  container mode creates `factory-suite-<issue>-<attempt>` from the task
  image with the holdout bind-mounted read-only over `/work`, no hook
  bundle, no key, removed afterwards. One builder repair
  (`FULL_SUITE_REPAIR_CAP`) counted against the attempt's repair cap,
  with the failing test ids first in the `VerifyOutput`; a second
  failure or an exhausted cap blocks with `full_suite`, the comment
  summary naming the failing tests and the gate row carrying the
  command output. `HoldoutPort` grew `tests_dir` and `copy_into`;
  `create_container` takes `extra_mounts`. Tests run real pytest on a
  real holdout clone (repair fixes, wrong fix blocks, cap exhausted,
  green) and check the container-mode mounts through the fake docker.
- 2026-10-24 — Unit 40, kill-switch: the flag of record is
  `installation.factory_enabled` (seeded by `FACTORY_ENABLED` at service
  start); `writer.require_enabled()` runs before every phase start and
  treats an unreadable or missing row as 0; a refused start cancels the
  task with the comment. `watchdogs.KillSwitch`: `disable` writes the
  flag, cancels every live task at once through the supervisor (the
  runner's cancellation terminates the process tree and removes the
  container), fails the open phase and process rows, marks tasks
  `cancelled`, comments with the last completed phase, kills containers
  still labelled `factory.task`, and writes one `control` event with the
  report; `enable` is a control with a reason that re-admits nothing;
  `run` re-reads the flag every `KILL_SWITCH_POLL_S` s. The scheduler
  dispatches nothing while disabled. `POST /controls/kill-switch` (TRD
  §6.1; loopback callers with `X-Factory-Actor` until the console token
  lands in phase 5; 400/403/409/503). `factory stop|start --reason
  [--by]` call the endpoint and fall back to the trace directly when no
  orchestrator answers. Test: three live sleeping phases end in about
  the SIGTERM grace, one control event, three failed phase rows.
- 2026-10-24 — Unit 41, budget service: `budget/watch.py` (`SpendGuard`,
  `run_guarded`): every proxied agent call is watched; the key's spend
  is read every `SPEND_WATCHDOG_INTERVAL_S`, projected onto the task and
  the day, and a cap writes `budget_stop`, cancels the call (process
  tree terminated; in a container the exec's cancellation now kills the
  container) and the phase blocks for budget with the proxy's spend as
  its cost. The runner's notification posts the spend comment with
  `factory:budget` plus `factory:needs-human`; `budget/controls.py`
  `raise_task_budget` (once, ≤ 2×, a `budget_raise` control the resume
  rule requires; resume removes `factory:budget`); `POST
  /tasks/{id}/budget` (loopback + actor; 404/409/422); `factory budget
  --task --raise-to --reason`. The scheduler holds the queue while
  today's spend has reached the daily cap (one log per day; clock
  injectable). `budget/dry_run.py` (`derive_budget`, `InstallLog`) and
  `factory dry-run --repo --issues a b c`: 2× the highest completed
  cost into `per_task_budget_usd` (mode `install`; the trigger
  recomputes the daily cap), refused below two completions or at zero
  cost, every derivation appended to `install.log` on the data volume.
  The budget comment now names the raise command and the resume
  gesture (snapshot regenerated).
- 2026-10-24 — Unit 42, harness-agnostic builder: the build slice runs
  end to end on both adapters from recorded streams (new
  `fixtures/streams/codex_build_green.jsonl`; an effectful replay writes
  the files the stream claims) with identical phases and gates and the
  harness and version on the phase row; `probe_harness_versions` at
  service start logs the roster (harness, model, vendor, version) and
  refuses an unreadable binary; a one-vendor roster fails at load and
  `factory serve` reports it with exit 2; a container start now writes
  the Codex execpolicy rules into `/home/agent/.codex/rules` when any
  role runs on Codex (`install_codex_rules_in`). Limit: Codex has no
  `--max-turns`; the phase cap and the timeout bound a Codex builder.
- 2026-10-24 — Unit 43, governed PRs and branch protection: the
  request side (unit 30) is proven from a committed `CODEOWNERS`
  (`PlanApprovals.owners_of` → `DeliverPhase` requests login and team);
  `github/protection.py` (`codeowner_review_required`,
  `check_branch_protection`) reads the repository rules API (Metadata
  permission) for a `pull_request` rule with
  `require_code_owner_review`, falls back to classic protection and
  treats a 403 there as unreadable; client `get_repository`,
  `get_branch_rules`, `get_branch_protection`; `factory protection
  --org --repo [--branch]` appends the verdict to the install log and
  exits 1 when the review is not required (unit 44's installer refuses
  to go live on it).
- 2026-10-24 — Unit 44, deployment bundle and installer: `deploy/`
  gains the orchestrator `Dockerfile` + `entrypoint.sh`,
  `docker-compose.yml` (orchestrator, litellm + postgres, caddy; litellm
  on `factory-tasks` as `proxy`; API on loopback for the CLI; console
  slot reserved), `Caddyfile` (internal listener with the internal CA,
  public listener routing only `/webhooks/github`), `factory.service`,
  README; `.dockerignore`. `cli/install.py`: `factory install` runs
  preflight, `/etc/factory` (App key, Ed25519 export key via
  `cryptography`, `factory.env`, `proxy.env` with the vendor keys for
  the proxy only), the data volume (hook bundle, clone with origin
  kept), migrations + installation row (export public key, version),
  the product-repo files on a `factory-install` branch through a
  temporary worktree, the self-test, the task image, the protection
  check, the dry run, compose up, the systemd unit, a `/healthz` probe;
  every step is an `install.log` line and a refusal stops before going
  live. `factory upgrade --pin --reason`: no-op on the same tag; else
  pause admissions (the scheduler now holds while
  `installation.admissions_paused`), drain ≤ 30 min, checkout,
  migrations, pull + up, self-test, eval slot (phase 4), version +
  resume, or rollback. Verified here: the installer end to end on the
  seeded repository with the fake harness (compose faked), the upgrade
  procedure with faked commands; the Linux-VM run with real compose is
  the phase 3 gate's (unit 46). Known gap for unit 45: in container
  mode the harness version must be read through the task container
  (the orchestrator image carries no CLI binaries).
  Verified on Docker Desktop: `docker compose config` renders the bundle
  and the orchestrator image builds and answers `factory --version` and
  `docker --version` (opt-in `tests/integration/test_deploy_docker.py`).
- 2026-10-24 — Unit 45, restart reconciliation: `TaskRunner.run` creates
  the worktree, reconciles with GitHub (the branch's PRs, the issue
  labels, the remote head; an open PR is adopted into `tasks.pr_number`
  when the row has none; findings logged on the run), then picks the
  resume point. New point `review`: a branch head equal to the trace's
  last build commit with no accepted review skips the build
  (`build_and_review(prior_build=)`), so a crash between commit and
  review no longer re-runs the builder on a finished tree. The
  plan-review announcement is once per spec sha
  (`writer.awaiting_logged`); a restart re-adds the label only. In
  container mode the harness versions are read through the task
  container (`read_harness_versions`) and handed to the adapters
  (`make_harness(version=)`; `HarnessFactory` takes the versions); the
  host probe at service start is worktree mode's. Proof: five crashes
  (build start, review start, deliver start, after `create_pull`,
  `ci_wait` start) plus a final start open one PR, post no comment, run
  each agent once, attempts 1 to 6, entering at plan, build, review,
  deliver ×3. Open point: a `full_suite` block still resumes at `build`
  (the fresh attempt re-runs the builder on the finished tree); the
  failing suite's `VerifyOutput` should seed that attempt.
- 2026-10-24 — Unit 46, phase 3 gate run
  (`tests/integration/test_phase3_gate.py`, each test printing one
  `GATE-EVIDENCE` line). Evidence on this machine:
  1. push, `gh`, internet (Docker Desktop, opt-in): a task container on
     the internal `factory-tasks` network with the hook bundle; hook
     denials `git push` 3/3, `gh` 3/3, `curl` 3/3; `https://api.github.com`
     unreachable 3/3; `gh` unavailable 3/3.
  2. five restarts (crashes at build start, review start, deliver start,
     after `create_pull`, at `ci_wait` start) + a final start: 1 PR, 0
     duplicate PRs, 0 duplicate comments, each agent run once.
  3. five issues per harness through the composition root with the real
     adapters over CLI-shaped streams synthesised from the seed scripts:
     builder on Claude Code → 5 `pr_open`, 15 agent phase rows (plan
     2.1.309, build 2.1.309, review codex 0.112.0), 40 `tool_call` rows;
     builder on Codex (reviewer on Claude Code) → 5 `pr_open`, 15 rows,
     40 `tool_call` rows.
  4. kill-switch through the composition root: a live sleeping builder
     stopped in 4.3 s (bound 60 s); task `cancelled`, one control event.
  The paid pilot-VM run with the real CLIs follows the runbook in
  `context/specs/46-phase3-gate.md` and is the user's.
- 2026-10-24 — Unit 47, eval runner: `orchestrator/eval/runner.py`
  (`EvalRunner`, `pass_rates`, `delta_points`, `ensure_eval_set`,
  `EvalError`); each item replays plan → build → review → full suite
  on a worktree of `eval/scratch` cut from the base branch, with no
  GitHub writes and no approval wait, its phases in a scratch trace
  under `data_dir/eval/<run_id>/` so the installation's tasks stay
  clean; `eval_runs`/`eval_results` in the installation's trace with
  pass rate overall and per class, the baseline (named or the latest
  complete run), the delta and `blocked` below −10 points; sets under
  20 items refused. `fixtures/eval/set.json`: 24 issues (101–124) over
  the ten seed tasks, 8 feature / 8 bug / 4 test / 4 docs. `factory
  eval run --fixture | --set NAME --trigger ... --baseline ...`. Proof:
  the fixture scores 24/24 with the fake harness; four seeded failures
  score 83.3, delta −16.7, blocked. Open: container-mode eval (phases
  in task containers) is not wired; the runner replays on the host.
  The 48-item fixture replay moved to `tests/integration/test_eval.py`
  to keep the unit suite under ten minutes.
- 2026-10-24 — Unit 48, KPI materialiser and audit queue:
  `trace/kpi.py` (`KpiMaterialiser`, `audit_sampled`, `ratio`) writes
  one `kpi_daily` row per metric and local day: the five headline
  metrics (`factory_merge_share`, `merge_rate`, `unattended_rate`,
  `audit_disagreement_low` with the high and governed series beside
  it, never pooled, `cost_per_merged_pr` with the 30-day median
  and the over-budget count) and the indicators (`repair_rounds_mean`,
  `escalation_rate`, `review_minutes_median`, `eval_pass_rate` overall
  and per class, `eval_gap_points`, `protected_path_attempts`,
  `stuck_runs_7d`, `verdict_override_rate`); empty denominators store
  NULL. The audit draw is a reproducible coin from task id and month
  (total on high and governed, 0.10 on low), one row per task and
  month; the merge observer draws the task and refreshes the day; a
  nightly loop materialises on each local day change;
  `writer.decide_audit` records the decision as an `audit` event.
  Open: the reviewer override rate is one series; the per-tier split
  needs the console's audit findings (phase 5).
- 2026-10-24 — Unit 49, budget switch to the rolling median:
  `budget/median.py` (`MedianBudget.refresh`/`run`, `measured_costs`).
  Inside 7 days of `installation.created_at` the install value stands;
  from day 8 the per-task budget is 2× the median spend of tasks
  admitted in the trailing 30 days whose run is over (`pr_open`,
  `merged`, `blocked`, `cancelled`, spend above zero), rounded to
  four decimals, `budget_mode='median'`, the daily cap by the
  `installation_daily_cap` trigger, one `control` event
  (`budget_median`) per move and nothing written when the value
  holds; no measurement keeps the install value and mode. The loop
  refreshes at start and on each local day change beside the KPI
  loop. Tests: day 6 holds, day 8 switches (median 2.0 → 4.0, cap
  36.0), same day unchanged, day 9 recomputes, tasks older than 30
  days drop out, the daily loop. Open: no floor (see Open Questions).
- 2026-10-24 — Unit 50, Slack notifications: `orchestrator/notify/slack.py`
  (`Notice`, `plan_review_notice`, `stuck_run_notice`, `SlackNotifier`,
  `NotifierPort`). A notice is `text` plus one section and one context
  block, nothing interactive; the footer says the approval is the
  GitHub label. `SlackNotifier.send` posts once to
  `FACTORY_SLACK_WEBHOOK_URL`, never raises, and writes one `log`
  event (`notify: slack`, kind, ok, status, error class or Slack's
  short reply); the URL never enters the trace or an exception text.
  `PlanApprovals(notifier=)`; the waiter sends the plan-review notice
  beside the GitHub comment under the `awaiting_logged` guard (no
  repost on restart) and still ends only on the label event.
  `Watchdog(on_stuck=)` awaits the hook per newly flagged process and
  the service now runs the watchdog loop (unit 14 built it; nothing
  ran it until now). Snapshots under `fixtures/notify/`. Without the
  URL the notifier is `None`.
- 2026-10-24 — Unit 51, retention, purge and signed export:
  `trace/retention.py` (`RetentionJob`, `null_before`, `null_task`,
  `delete_metadata`, `purge_task`, `purge_login`, `pseudonymise`,
  `purged_logins`) and `trace/export.py` (`build_export`,
  `verify_export`, `load_private_key`, `public_key_b64`). The job
  runs once a day from 03:00 installation time: payloads older than
  `retention_days` nulled in `events`, `envelopes`, `gate_results`;
  events older than the year plus a day deleted when no child event
  or audit decision references them, then unreferenced phases and
  runs with their processes, envelopes and gate results; one `purge`
  event per day with counts, seq range and cutoffs. `factory purge
  --task T` nulls at once; `--login L` writes a `purge` event holding
  only the SHA-256 and the trace listing and the export show
  `user:<12 hex>` from then on. The export is one canonical line per
  event (covered record, stored hash, payload or null) and a trailer
  signed with the install key over its five fields; `verify_export`
  checks signature, head, range, record and payload hashes and the
  links of a contiguous range. `GET /tasks/{id}/export` (loopback +
  `X-Factory-Actor`, 404/503/200, an `export` event) and `factory
  export [--task|--from --to] [--out] [--key]`;
  `Settings.export_key_file` written by `factory install`. Open: a
  pseudonymised line keeps its stored hash, which a verifier cannot
  replay without the login; the console applies the same mask in
  phase 5.
- 2026-10-24 — Unit 52, second installation and phase 4 gate run:
  `factory eval add --set --repo --items FILE.json [--reference-pass-rate]`
  records a set from JSON idempotently; `factory eval run
  --reference-pass-rate` sets the reference; `EvalReport` carries
  `reference_pass_rate` and `gap_points` (printed as within or wider
  than `EVAL_GAP_POINTS` = 10; the gap never blocks); writer `eval_set`,
  `set_eval_reference`; the seed scripts price a task by its seed, not
  by the issue number it replays under (the fixture's items 101 to 124
  were costing 1.31 to 1.54 a build). `tests/integration/
  test_phase4_gate.py` (offline, fake harness) records the gate:
  condition 1 fixture replay on the pilot 24/24 passed, 100.0 overall (bug 100.0, docs 100.0, feature 100.0, test 100.0);
  condition 2 three issues label → PR → merge through the composition
  root, cost per merged PR $0.53 against a 30-day median of $0.53 (ratio 1.0), 0 over budget; condition 3 a second
  scratch repository, its roster caps set to 0.20 / 0.40 / 0.20 and N
  to 10 for a repository whose tasks cost cents, installed with the fake
  harness (1 host command recorded, dry-run budget $0.76), its set
  replayed at 100.0
  against the pilot's 100.0: gap +0.0 points. The paid conditions (two
  pilot weeks of cost; the real second in-house repository's own 90-day
  set) are the user's: runbook in `context/specs/52-phase4-gate.md`.
- 2026-10-24 — Unit 53, read API and streams: `models/api.py` (the
  response schemas; lists `{items, next}`, errors `{code, message,
  detail?, traceId}`), `api/limits.py` (`identify`: service token +
  `X-Factory-Actor`, loopback until the console lands, else 403;
  `RateLimiter` token buckets per login with the TRD rules; five tail
  streams per login; 429 names the rule), `api/read.py` (every read
  endpoint of TRD §6.1 under `/api/v1`: installation, tasks with a
  200-row cursor, task detail with runs, phases, approvals, budget,
  links and the chain footer, events paged by seq, envelopes, gates,
  review rounds from the `verdict_policy` logs, plan queue with spec
  text from the clone and the AC→holdout checks, questions with
  language, audit queue, KPIs and indicators from `kpi_daily` with
  targets and lines, needs-human count, eval sets and runs, config
  and history), `api/streams.py` (SSE tail with `id: seq`, resumable
  through `after=`, ping after 15 s; `/changes` notices),
  `api/openapi.py` + `factory openapi [--check]` + the committed
  `openapi.json`; `Settings.service_token` written by `factory
  install`. Tests: every endpoint, limits, the tail within 2 s,
  the document matching, schemathesis (integration), Overview reads
  under 2 s with 1,000 seeded tasks (integration). Open: the three
  write endpoints of units 40, 41 and 51 stay unprefixed until unit 55
  moves them under `/api/v1` with the authz check; the chain footer
  verifies the task's whole seq span, which grows with the trace.
- 2026-10-24 — Unit 54, authorisation table: `authz.yaml` (five
  permission levels, fourteen actions, `always` and `codeowner`, three
  rule groups, fourteen cases), `orchestrator/authz/` (`load_table`
  validates and hashes; `decide` answers from the generated module with
  default deny and a named reason; `verify_generated` stops
  `build_service` on a stale or broken table; `resolve_permission`
  caches GitHub's answer five minutes and returns `None` when GitHub is
  unreachable, which denies), `generate.py` rendering `authz.py`,
  `console/lib/authz.ts` and `authz.cases.json` deterministically,
  `factory authz [--check]`. Tests: every case row against `decide` and
  `allowed`, the committed files match, refusal cases, the cache and
  the fail-closed path. Pending: the vitest run of
  `console/lib/authz.test.ts` once unit 56 brings the console project;
  unit 55 wires `decide` into the write endpoints.
- 2026-10-24 — Unit 55, control endpoints: `api/controls.py` runs one
  order for every write (parse the pydantic request, identify the
  caller, resolve the GitHub permission, `authz.decide` with a 403 and
  a `denied` control on refusal, `Idempotency-Key` required and
  replayed from `idempotency_keys` (migration 0005; another body under
  the same key is 422), the rule's rate limit, the service, one
  `control` event under the login, the remembered response):
  `POST /api/v1/tasks/{id}/budget` (once, at most 2×), `/resume`
  (`Resume.apply`, the §7.8 rule shared with the label door),
  `/risk-override` (until `pr_open`; `risk_overridden_by`, an
  `approvals` row with gate `risk_override`), `POST /api/v1/audit/{id}`
  (404 not drawn, 409 decided), `/controls/concurrency` (1 to 10),
  `/controls/kill-switch` (1/min, 409 in state), `/controls/admissions`
  (pause, resume, `clear_incident`), `POST /api/v1/eval/runs` (202 with
  the run id; 404, 422 under 20 items, 409 while one runs; 2/h). The
  unit 40, 41 and 51 routes moved under the prefix; the service hands
  the router its scheduler, kill-switch, resume door, permission
  resolver, CODEOWNER check and eval starter. Open: the risk override
  re-enters the approval wait on the task's next attempt (the running
  attempt is not interrupted); the incident the admissions control
  clears is the `incident` event seq the operator names.
- 2026-10-24 — Unit 56, console scaffold: `console/` is a Next.js 15 App
  Router project (TypeScript strict with `noUncheckedIndexedAccess` and
  `exactOptionalPropertyTypes`, ESLint, no Tailwind, vitest + jsdom);
  `styles/industry.css` vendored from the design package and
  `styles/console-tokens.css` (dark theme, the five `--sem-*` roles,
  `--font-mono`, the density numbers, reduced motion); `lib/api.ts` (the
  one orchestrator client: service token + `X-Factory-Actor`, no-store for
  live reads, tags for config and eval, `ApiError` from the standard body)
  with `lib/api.types.ts` generated from `openapi.json` (`npm run
  api:types`); `Shell` (installation name, six-screen nav with the
  Approvals badge, the `FACTORY_ENABLED` pill from `/healthz`, a login
  pill placeholder), `ThemeRoot`, `KeyboardMap` (`1`–`6`, `?`),
  `Freshness`, `ViewportGuard` (under 1280 px to `/unsupported`); the six
  routes render their titles over a `Placeholder` frame naming the
  components later units bring; the unit 54 authz cases run in vitest.
  `npm run lint`, `typecheck`, `test` and `build` pass.
- 2026-10-24 — Unit 57, sign-in and sessions: `lib/auth.config.ts` (the
  edge-safe Auth.js half: the GitHub App's user-to-server provider,
  `basePath /auth`, an 8-hour JWT cookie carrying only sid, login and
  permission, `authorized` keeping `/auth/*`, `/unsupported` and the
  health probe public), `lib/auth.ts` (the node half: the `jwt` callback
  looks the permission up with the user's token, stores the token AES-GCM
  encrypted in `console.db`, the `signOut` event deletes the row and
  revokes the token at GitHub; `loadSession`, `userToken`),
  `lib/sessions.ts` (`SessionStore` over `node:sqlite`, 8 h, expiry on
  sight and `sweep`), `lib/github.ts` (`addLabel`, `removeLabel`,
  `viewerPermission`, `revokeToken`; refusals name the fix), `lib/bff.ts`
  (the handler order: zod, session, authz, act, standard error body),
  `app/auth/[...nextauth]/route.ts`, `middleware.ts`; `next-auth@beta`.
  Tests cover the store, the GitHub writes and the BFF order; the OAuth
  flow against the real App is the user's runbook in
  `context/specs/57-signin-sessions.md`.
- 2026-10-24 — Unit 58, primitive components: `Chip` (one anatomy per
  enumerated value, role and glyph tables from the UI context, `role="img"`
  with "Task state: blocked" labels, solid `Blocker`, dashed `skipped`
  and `Unverifiable`, risk bars, label chips, disabled at 45%), `Meter`
  (`role="meter"`, "$46.10 of $54.00, 85 percent", tone and glyph
  together at 80% and at the cap, loading and error), `KPITile`
  (`role="group"` with a full label, 40 px value or `—`, seven trend
  bars, the five statuses, target text), `ScoreBreakdown` (base × factor
  rows, the equation box, the outcome chip, override login and reason,
  tier U); `primitives.test.tsx` renders every state, checks the spoken
  labels and runs axe with zero violations (contrast disabled under
  jsdom). Note: severity glyphs beyond `Blocker` (`Major ■`, `Minor ●`,
  `Nit ○`, `Unverifiable –`) follow the role glyphs because the UI
  context names none for severities; recorded here, not invented silently.
- 2026-10-24 — Unit 59, Overview: `lib/overview.ts` (`loadOverview`:
  the installation, KPIs, indicators, plan queue, blocked, running and
  queued tasks and the count in one live read, each failure named;
  the five headline tiles with the PRD targets, the cost tile judged
  against twice the median and the over-budget count), `KpiRow`,
  `NeedsHumanTable` (plans waiting and blocked tasks, 36 px rows),
  `RunningQueuedTable` ("Queued, n ahead"), `SpendByTask` (a `Meter`
  per running task), `IndicatorsPanel` (collapsed by default, the
  state remembered per browser), `StuckFlag` (from `stuck_runs_7d`; the
  pulse stops under reduced motion), `NavBadge` (30 s poll of the
  console's `/api/console/needs-human` read proxy behind the session),
  the "Spend today" meter against the daily cap beside the
  `FACTORY_ENABLED` pill (the dialog is unit 65). The installation read
  gained `spent_today_usd` (today's phase spend, the TRD's Overview
  meter input the endpoint table did not name; `openapi.json` and
  `api.types.ts` regenerated). Playwright: `console/e2e/overview.spec.ts`
  loads the Overview against an orchestrator API seeded with 1,000 tasks
  and asserts under 2 s and the collapsed panel. GATE-EVIDENCE
  console.overview (2026-10-24, local, Chromium): 10 timed loads after one
  warm-up, ms 231 274 281 282 284 294 331 343 376 402, p95 402 ms. The run
  signs in by minting a console.db session and its Auth.js cookie
  (`e2e/global-setup.ts`); the API is `tests/e2e/serve_seeded_api.py`.
- 2026-10-24 — Unit 60, Task detail and live trace: `lib/task.ts`
  (`loadTask`: the detail, then the newest event page, envelopes, gates,
  the risk and override events and the queue position in parallel, each
  failure named; `riskProps` for the risk `ScoreBreakdown`),
  `lib/hooks/useTail.ts` (EventSource on the console's SSE proxy resumed
  from the last `seq`, 4 s poll fallback, "Tail behind by N s" past 5 s,
  pause and resume without losing rows, earlier pages merged in),
  `TaskStrip`, `PhaseTimeline` (shared axis; agent solid, code outline,
  human hatched; running extends to now; "Queued, n ahead"),
  `GateLadder` (gates as run; after a failure the phase's remaining gates
  read `–` skipped with no button; first failure's `EvidenceDrawer` open;
  `Esc` closes), `RepairRounds` (failed code-gates runs per build attempt
  against the cap of 3), `EnvelopeViewer` (Radix Tabs per agent and
  builder attempt; invalid shows the error paths and raw text; empty
  reads "No envelope; phase did not return."), `ChainFooter`,
  `CopyJson`, `TraceTable` (newest first, 2 s tint, `p` toggles, 400-row
  DOM window, earlier pages on scroll or button), the task budget
  `Meter`, the page under `/tasks/[taskId]`, the BFF read proxies
  `/api/console/tasks/[taskId]/{events,stream}` behind the session.
  Orchestrator: `before=<seq>` on the events read (newest first, the
  TRD §3.4 first paint), `head_seq` on the chain footer,
  `instrument_version` in the risk payload; `openapi.json` and
  `api.types.ts` regenerated. Playwright `e2e/task-detail.spec.ts`
  against the seeded task with 10,000 rows. GATE-EVIDENCE (2026-10-24, local,
  Chromium): task_detail 10 timed loads after one warm-up, ms 1048 1054 1061 1121 1146 1150 1306 1477 1607 1796,
  p95 1796 ms; live_tail new row within 249 ms; trace_pages
  10000 rows loaded in 16734 ms; `p` pauses and resumes; the two gates
  after the lint failure carry no button and its evidence opens first. Of the
  detail read, about 0.8 s is the chain verification over the task's rows
  (TRD §4.3 footer); noted for a later cache, not changed.
- 2026-10-24 — Unit 61, Review findings: `lib/review.ts` (`loadReview`:
  the task detail and the round's review, `review_pending` and a bad
  round named; `orderedFindings`: blocking first, then advisory, then
  dismissed; `acRows` from the reviewer's envelope plus `missing_ac`;
  `reviewProps` for the review `ScoreBreakdown`; `blobUrl` for
  locations on GitHub), `ReviewHeader` ("Conformance 44 · accept ≥ 70 ·
  round 1 of 2", the verdict and the reviewer's own verdict, the human
  reasons, the override), `AcTable` (one `met` verdict per AC with its
  evidence; unaddressed ACs read "not addressed"), `FindingsList`
  (Severity, Req., Location, Problem, Adjustments, Score, Effect; a
  dismissed row at 60% opacity quotes its Non-goals line; empty reads "No
  findings. Every AC met."), `RoundSwitcher` (`?round=` links), the page
  under `/tasks/[taskId]/review`. Orchestrator: the scored finding
  payload carries `base`, `spec_link`, `evidence` and `scope` and the
  verdict payload and review read carry `accept_conformance` (the
  policy's threshold the header and conclusion name); `openapi.json`
  and `api.types.ts` regenerated. vitest covers the ordering, the quoted
  line, the AC rows, the header and the breakdown; axe clean.
- 2026-10-24 — Unit 62, Approvals, audit and label writes:
  `/approvals?tab=plans|questions|audit`; `lib/approvals.ts` (the three
  queues, the spec's sections, the AC→holdout table from the spec gate's
  checks, the audit strata, and what the viewer may do with each plan:
  `plan_approve` or `governed_approve` through the authz table, with the
  disabled reason named), `lib/codeowners.ts` (the orchestrator's CODEOWNERS
  rules in TypeScript; the file read from GitHub at main under the user's
  token, cached 60 s), `PlanQueue`, `SpecView` (sections, AC→holdout with
  existence checks, the risk breakdown), `ApprovalCard` (writes
  `factory:plan-approved` through `POST /api/console/labels` under the
  user's token; 409 when the spec sha changed; 429 past 30 a minute; GitHub's
  refusal passes through and nothing is recorded; `a` focuses the button;
  "The GitHub label is the approval."), `QuestionsTable` (`lang` per cell),
  `AuditStrata`, `AuditTable`, `AuditDecisionRow` (decision and Reason,
  proxied to `POST /audit/{task_id}` with an Idempotency-Key),
  `RiskOverrideDialog` (Radix Dialog, Reason required, proxied to
  `POST /tasks/{id}/risk-override`); cancel and resume as label removals
  through the same labels route. `lib/bff.ts` resolves the action from
  the body and passes orchestrator refusals through; `lib/api.ts` gained
  `postJson`; `lib/ratelimit.ts` holds the console's per-login write
  limits. The GitHub-side check (an approval on the issue by the test
  user, the `approvals` row bound to the spec sha) waits on the GitHub App
  authorisation and the scratch repository (user items).
- 2026-10-24 — Unit 63, Eval and versions: `lib/eval.ts` (sets, runs,
  config history and installation in one read; runs grouped by factory
  version and configuration with the current one marked; a run's result
  for its change (`blocked` on a drop over 10 points, else passed,
  running, failed); per-class regressions against the baseline and the
  result line "Overall 63 → 50 (−13 pt). bugs 67 → 44."; the latest
  complete run per set and its gap to the reference pass rate),
  `EvalSetSummary`, `EvalComparisonTable` (Version, Trigger, Overall,
  bugs, tests, docs, small features, Δ overall by role, Result),
  `RunsByVersion`, `BlockedChange`, `InstallDryRun` (budget mode,
  per-task budget, daily cap N × 3 × budget), `RunEvalButton` (posts a
  manual replay through `/api/console/eval/runs`, proxied to
  `POST /eval/runs` with an Idempotency-Key; disabled without `eval_run`
  or under 20 items, the reason named). The dry run's derivation record
  stays in the VM's install log, which no TRD endpoint exposes (open
  question below).
- 2026-10-24 — Unit 64, Configuration and change PRs:
  `/config?tab=roster|instruments|budgets|paths|installation`;
  `lib/config.ts` (the loaded configuration, history, installation and
  eval runs with tagged reads; roster rows, quality commands, the PRD
  protected set marked apart from additions; the find/replace edit rule;
  the branch name, PR title and body; the open `factory-config/*` PRs
  with their diffs under the user's token, cached 60 s; the eval run
  matched to a PR by number), `lib/changes.ts` + `instrumentation.ts`
  (the `/changes` listener revalidating `config`, `config:<sha>`, `eval`,
  `eval:<run_id>`; `lib/api.ts` caches tagged reads), `OpenChanges` with
  `ConfigChangeCard` (file, PR link, diff lines, the eval line "Overall
  63 → 50 (−13 pt). bugs 67 → 44.", "Eval running", the error role when
  blocked; footer "Opened by @login · reason · not applied until merged"),
  `RosterTable`, `InstrumentTables` (tech-lead class noted),
  `BudgetsTable`, `ProtectedPathsTable`, `InstallationPanel`,
  `ProposeChangeDialog` (file, find, replace, Reason; the file at main
  shown) and `POST /api/console/config-pr` (10 a day per login; 422 when
  an edit is missing, ambiguous or changes nothing, or the branch exists;
  branch, commit and PR under the user's token; nothing applied before
  merge). The GitHub-side check (a PR under the test user with its eval
  result on the card) waits on the GitHub App authorisation and the
  scratch repository (user items).
- 2026-10-24 — Unit 65, Runtime controls and the kill-switch dialog:
  `KillSwitchDialog` (header pill "FACTORY_ENABLED=1 · n live processes",
  "Stop factory…" outlined in the fail role; the dialog states the effect,
  needs a one-line Reason with focus in it, `Esc` cancels, Enter confirms
  only when filled, "Stopping n processes · s left" counts down from 60 s,
  disabled without write access or when already 0 with who and when; the
  same dialog re-enables), `ControlsPanel` (concurrency 1 to 10 with a
  Reason; admissions pause and resume with a Reason, the open incident
  cleared on resume), `BudgetRaiseDialog` on the task detail (once, at
  most 2×, with a Reason), each through a BFF proxy to the control
  endpoints with the actor and an Idempotency-Key, every answer the
  orchestrator's own (403, 409 already in state, 422 bounds). Orchestrator:
  `/healthz` carries `live_processes`; the installation read carries
  `open_incident_seq` and `last_kill_switch` (actor, time, state, reason),
  the dialog's two reads the TRD names but no table carried; `openapi.json`
  and `api.types.ts` regenerated. `tests/e2e/serve_seeded_api.py` wires a
  `KillSwitch` (no supervisor) so the Playwright check can stop and restart
  the factory offline. GATE-EVIDENCE (2026-10-24, local, Chromium):
  kill_switch stop 87 ms from Enter to the recorded events
  row, factory_enabled false, live processes after 0, the running
  task cancelled; the budget raise, concurrency and admissions controls each
  read back their events row with the e2e login; the same run re-measured the
  Overview at p95 368 ms and the Task detail at p95 1379 ms, a new row
  within 1923 ms, 10000 rows walked in 37057 ms.
- 2026-10-24 — Unit 66, Phase 5 gate run: `tests/e2e/serve_seeded_api.py`
  grew into the gate fixture (three running tasks gaining rows, a plan
  waiting, the shipped `.factory/` files as two configuration versions, an
  eval set with a baseline and a blocked run, risk classes on the merged
  tasks for the audit queue, a loopback webhook door with a stand-in
  admission); `console/e2e/screens.spec.ts` (the six screens in light and
  dark through axe-core, WCAG 2.2 AA tags; the keyboard map: `?`, 1 to 6,
  j/k, Enter, Esc); `tests/load/{overview,task_detail,admission}.js` (k6)
  and `tests/load/sse_lag.mjs` (Node; k6 has no SSE client) with the
  runbook in `tests/load/README.md`; the keyboard map's row keys made real
  (`j`/`k` move between `[data-row]` rows, Enter opens the row's link or
  button; the tables and ladders mark their rows; one focus ring). The
  gate's first pass found and fixed: the light theme's pass, fail and warn
  roles and the accent on the current nav link, ghost buttons and card
  kickers under 4.5:1, and the Industry sheet's own light ink levels (links
  and primary buttons on the 3.7:1 accent, table headers at 60% ink,
  `.text-muted` at 55%), now a strong accent token and 70% muted ink in
  `console-tokens.css` (5.5:1 or better); scrollable evidence, envelope,
  instrument and trace regions that could not take focus (`tabIndex=0`);
  dismissed findings at 60% opacity under contrast in dark (now muted ink,
  a noted deviation from the UI context's opacity); and the task detail
  read verifying the whole chain span on every request, which put the k6
  p95 at 2.5 s under three users (now an incremental verification per
  task in the process; `factory verify` and the export recompute every
  row). The
  tech-lead run (10 approvals, one audit cycle, labels under their own
  login) is written up in the spec for the user's GitHub run. GATE-EVIDENCE
  (2026-10-24, this PC, Chromium, k6 v2.3.0, Node 24): condition 2 Overview
  Playwright p95 1242 ms over 10 loads with 1,000 tasks, k6 p95 43 ms over
  405 requests; Task detail Playwright p95 215 ms with 10,000 rows, k6 p95
  29 ms over 180 requests. Condition 3 axe WCAG 2.2 AA over 16 views
  (8 screens × light and dark): 0 critical, 0 serious, 0 moderate, 0 minor;
  the keyboard map (?, 1 to 6, j/k, Enter, Esc) passes. Condition 4 kill-switch
  from the console: stop 100 ms to the recorded events row, the running task
  cancelled, live processes after 0 (unit 65; the live-task SIGTERM
  bound is the phase 3 gate's). Load: SSE lag under 3 running tasks p95
  510 ms over 89 rows in 60 s (max 513 ms); a new row in the tail
  within 1945 ms; admission with 50 label events in a minute p95
  6 ms (max 7 ms, accepted rate 1). The GitHub
  half of condition 1 waits on the user (Next Up).
- 2026-10-25 — Unit 67 (amendment), budget decisions: the user decided the
  four open budget questions. (1) The install derivation applies the
  industry per-task cap: `derive_budget` never returns less than US$5
  (`INDUSTRY_PER_TASK_CAP_USD`; the survey behind the figure is in spec 67:
  caps of US$3 to 5 in CI use, measured per-task spend of US$0.70 to 5, a
  Devin bug fix at US$4.50 to 6.75), and records `floor_usd`; a cheap dry
  run (0.32 / 0.34) now derives 5.00 instead of 0.68. (2) The median rule
  stays as the PRD writes it, no floor. (3) An eval replay's scratch trace
  takes its own ceiling, items × the per-task budget (`set_daily_cap`), so
  the 24-item set fits without raising N; the run's log event carries
  `cap_usd` and `spent_usd`. (4) The installation read carries
  `last_dry_run` (the three issues, their costs, the factor, the floor, the
  reason) and the console's Install dry run panel lists them. The build plan
  gained an amendments section; `project-overview.md` names the floor. The
  tests that asserted the un-floored figure now assert the floor, and
  `tests/conftest.py` points `FACTORY_ENV_FILE` at an empty file for the
  session so the operator's new `app/.env` no longer leaks into the suite
  through the CLI's `main()`.
- 2026-10-25 — fix(api), after unit 67: the schemathesis contract test
  (`tests/integration/test_api_contract.py`, replaying its stored examples
  from `.hypothesis/`) failed on `main` before and after unit 67 for three
  reasons, now fixed. A path the router does not know (a path parameter
  holding a newline matches no route) and a method it does not serve answered
  with Starlette's `{"detail": ...}` body; both now take the error body the
  TRD requires everywhere (`code` `route` and `method`), and a 405's `Allow`
  lists the methods of every router on the path's template (`GET, POST` on
  `/api/v1/eval/runs`, which the read and control routers share; `GET` alone
  on `/audit/queue`, which `/audit/{task_id}` also matches). The export route
  now documents its answers (200 as `application/x-ndjson`, the shared error
  responses), so `openapi.json` and `console/lib/api.types.ts` changed. A denied
  export or control on a task the trace does not hold wrote a control event
  linked to that task and failed the foreign key (a 500); the denied event is
  now unlinked and names the task in its payload. `tests/unit/test_api_errors.py`.
- 2026-10-25 — fix(runner), found by the scratch gate run on this PC. A worktree
  path the clone still registered after its directory had been wiped came back
  from `create_worktree` as if it existed; the runner then failed on git before
  its own try block, the asyncio task died unobserved, and the task stayed
  `running` with no phase and no error event. `create_worktree` now prunes and
  recreates a registered path whose directory is gone, and the runner blocks a
  task with an `error` event, a comment and `factory:needs-human` when anything
  fails before the run row (`_blocked_before_run`), mapped to `timeout` like
  every other unexpected error. `tests/unit/test_worktree.py`,
  `tests/unit/test_runner_prerun.py`.
- 2026-10-25 — Rebrand to Tank Factory v1 and publication, the user's decision:
  every mention of the former company brand leaves the repository (the product name, the company in
  prose, the example organisation in tests and fixtures, now `acme`, and the
  GitHub user agent, now `tank-factory/`); the PRD and TRD are copied,
  rebranded, into `docs/` and the context files point there instead of
  `../specs/` (the originals there stay untouched, and the Claude Design
  package is not published); `README.md`, `LICENSE` (MIT),
  `docs/GETTING_STARTED.md` and `docs/templates/holdout-conftest.py` are new;
  `.env.example` no longer carries a local path. The public repository starts
  from one clean commit, so none of the earlier history or author email is
  published; this local repository keeps its full history.
- 2026-10-25 — fix(workspace), found by the mdedit pipeline run: the factory
  never fetches its repository clone, so a task admitted after a merge got a
  branch cut from the clone's stale `main` (issue 7's planner, started a minute
  after the history module merged, rightly asked where `History` was).
  `create_worktree` now fetches the default branch and cuts a new branch from
  `origin/<default>`; an existing branch keeps its fork point (merge-base) as
  its base, so its diff never shows merged work as its own and the claim gates
  stay true. `tests/unit/test_worktree.py`.
- 2026-10-25 — fix(runner), found by the mdedit pipeline run: TRD §7.8 resumes
  `repair_cap | full_suite | timeout` as a new attempt on the same branch
  head, but `resume_point` sent only `timeout` and `revision_cap` to review when
  the gated build was committed at the head; `repair_cap` and `full_suite`
  re-entered the builder, which found nothing to change and blocked again
  ("the builder reported success but changed nothing"). Both now resume at
  review like the other two; a head that moved past the committed build is
  still built again. `tests/unit/test_runner.py` (fails on the old rule for
  exactly the two added reasons).
- 2026-10-25 — fix(trace), found by the mdedit pipeline run: Claude Code's
  `total_cost_usd` is the session's running total, and the factory resumes the
  same session for infra retries and repair rounds, so each resumed call booked
  the session's whole cost again (issue 4's build: US$0.56 of spend booked four
  times). Task spend, the day's spend and the budget checks all read those
  sums, so a repair loop would hit its cap early. `HarnessResult` now says when
  a cost is a session total (`cost_is_session_total`, set by the Claude Code
  harness) and `PhaseRecorder.finish` books only the part beyond what earlier
  phases of that session booked (`TraceWriter.session_cost_usd`). Codex reports
  no cost and the fake harness per-call costs; neither changes. The mdedit
  trace's rows before the fix overstate: US$4.58 booked for US$2.04 spent.
  `tests/unit/test_session_cost.py`.
- 2026-10-25 — mdedit pipeline paused by the vendor: every Anthropic call
  answered "Credit balance is too low" from 20:50 local; the three builds
  (issues 4 to 6) and three plans (1 to 3) each retried three times and blocked
  as `timeout`, with a comment and `factory:needs-human`; removing that label
  starts a new attempt once the account has credit (operator action).
- 2026-10-25 — fix(console) layout and dialogs, from the user's review of every
  page: screenshots of all views and dialogs in light and dark at 2560 and
  1440 px against the e2e fixture found every dialog see-through (industry.css's
  later blueprint rule `.card, .dialog { background: transparent }` cancels its
  own dialog surface) and unpadded (four dialogs read an undefined `--space-5`;
  the scale is 1, 2, 3, 4, 6, 8), a dark backdrop that dimmed nothing, the
  Overview's kill-switch row touching the tiles (its `controls` class was
  defined nowhere), the rail off the tile grid, the two Overview tables and the
  two Eval comparison tables sizing their columns independently, the controls
  card wrapping one button and not the other, and two flex table cells (Review's
  Finding, Eval's Result) detaching their row's border. Fixed in the console's
  sheet and modules: dialogs on `--color-surface` with `--shadow-lg` and a
  stacking order, `--space-6` padding, a black dark-theme scrim, the Overview on
  the KPI row's five-column grid (main spans four, rail the fifth), fixed shared
  table columns, consistent control rows, inner flex wrappers in the two cells,
  and the TraceTable's undefined `sentinel` and `end`. axe over the 16 gate views
  stays at 0 critical and 0 serious. `components/global-styles.test.tsx` now
  fails on an undefined CSS variable, an undefined module class, a table cell
  taken out of table layout, or a dialog without a surface (each checked
  against the pre-fix files).
- 2026-10-25 — fix(console), found by the user on the task detail: the
  `sr-only` class was used by PhaseTimeline, ControlsPanel, AuditDecisionRow
  and RunEvalButton but defined in no sheet, so screen-reader text rendered
  visibly and spilled over the phase rows and the Gates heading; and a
  phase's meta ("2 min 17 s · $0.49 · claude_code claude-opus-5-5") wrapped
  out of its fixed 30 px row. `console-tokens.css` now defines the visually
  hidden pattern; the meta is one line with an ellipsis and the full text in
  its title. axe found neither (no rule covers visible overflow).
  `components/global-styles.test.tsx`.
- 2026-10-25 — fix(harness), found by the first paid run (spec 3 of the mdedit
  project): the builder stalled for 90 minutes with no trace event after
  17:31 while its worktree showed it kept working until 17:38. The runner
  read stdout with asyncio's `readline`, whose 64 KiB line limit raised on a
  long stream-json line (a model turn that writes a whole module); the
  reading task died unobserved, the child blocked on the full pipe, and on
  Python 3.12 `proc.wait()` stays pending until the pipes close, so even a
  kill would not end the wait before the 7200 s timeout. `run_with_timeout`
  now reads stdout in chunks and splits lines itself (a line past 32 MiB is
  handed on truncated, which the stream parser counts as unparsed), always
  drains both pipes to the end, and when the line handler raises it records
  the failure, stops the tree at once and ends the stderr tail with it.
  `tests/unit/test_process.py` (a 300 KiB line; a raising handler under a
  flood of output returns in seconds).
- 2026-10-25 — Console sign-in against the real App (spec 57 runbook, write
  half): the user signed in at `http://localhost:3100` through the App's
  user authorisation. `console.db` holds one session for `aqueirozbr-vibez`
  with permission `admin`, expiring eight hours after creation; its token
  column is 68 bytes of AES-GCM ciphertext with no plaintext GitHub token
  in it. On this PC Auth.js names its callback `localhost` whatever host the
  browser uses, so the App registers `http://localhost:3100/auth/callback/github`
  next to the `127.0.0.1` ones.
- 2026-10-25 — Phase 2 gate on GitHub, passed. App `tank-factory` installed on
  the public scratch repository `aqueirozbr-vibez/tank-factory-v1` (`main`
  protected by a ruleset requiring code-owner review; CODEOWNERS names the
  user). `factory dev issues` opened issues 1 to 11 under the App;
  `factory serve --harness fake` with the user's `factory:ready` labels
  opened 10 PRs (#12 to #21) labelled `factory:review` and `risk:low`, and
  blocked issue 11 with one question (`open_questions`). The user approved
  and merged 8 PRs (#12 to #19) under their own login; the poller recorded
  8 merge deliveries and the 8 tasks read `merged`; #20 and #21 stay open.
  Every agent phase ran on the fake harness (31 phases, scripted cost
  US$5.77, no virtual key, no vendor call, US$0 real spend); the hash chain
  verifies. Three defects found on the way are the fix entries below. The
  first pass's stray `Blocked: timeout` comments on issues 1 to 11 (the
  proxy 401, before the trace was reset) remain on GitHub.
- 2026-10-25 — fix(poller), found by the scratch gate run: a dropped read on the
  reviews call (`httpx.ReadError`) escaped the poller's per-sub-poll isolation,
  which caught only `GitHubApiError`, and took the whole service down with it.
  The GitHub client now retries transport errors with the infra backoff (3
  retries, 5/20/60 s) and raises `GitHubApiError` with status 0 when the
  connection stays dead; the poller isolates any exception per sub-poll and
  records a failed tick without leaving its loop.
  `tests/unit/test_github_transport.py`, `tests/unit/test_poller.py`.

## In Progress

- None.

## Next Up

1. The real-harness phase 1 run (user decision: it spends money). Command
   in `context/specs/16-phase1-gate.md`; record cost and stream shapes.
2. The phase 5 gate's GitHub half (user): the tech-lead run of 10 plan
   approvals and one audit cycle from the console with every label under
   their own login; runbook in `context/specs/66-phase5-gate.md`.
6. The phase 4 gate on the pilot (user, paid): two weeks of merges for
   cost per merged PR, and the second installation's own eval set;
   runbook in `context/specs/52-phase4-gate.md`.
5. The phase 3 gate on the pilot VM with the real CLIs (user, paid):
   runbook in `context/specs/46-phase3-gate.md`.
3. Done 2026-10-25: the phase 2 gate on GitHub (evidence under Completed).
4. Paid runs: `app/.env` holds `ANTHROPIC_API_KEY` since 2026-10-25;
   `CODEX_API_KEY` and the codex CLI are still missing (authorised up to
   US$100 in total; nothing spent).
7. The console sign-in against the real App (spec 57 runbook, user): the
   write-user half passed on 2026-10-25 (below); left are signing in as a
   read-only collaborator (needs a second GitHub account) and the
   sign-out check that GitHub then answers 404 for the token.

## Open Questions

Product (from the PRD; the TRD's assumption applies until decided):

- Commercial model for client installations: support retainer (assumed),
  per-installation licence, or bundled into an engagement.
- Triage agent timing: not before a second installation has one month of
  eval history (assumed).
- Phase-4 second installation: a third, smaller repository first (assumed),
  or the busier in-house repository.
- Holdout tests written ahead of their code (full suite): the full-suite phase
  copies the whole holdout `tests/acceptance/` into a task's worktree and runs
  the repository's full suite, so a holdout file for an issue still in flight
  fails every other task on import (mdedit, 2026-10-25: issues 1, 2 and 6
  blocked `repair_cap` on the other issues' tests; the builder rightly changed
  nothing). The PRD says the holdout is added per issue before `factory:ready`
  and names no rule for parallel issues. The mdedit holdout now carries a
  `conftest.py` that always runs the task's own file (from the `agent/<n>`
  branch) and another issue's file only once its module exists. Whether the
  factory should scope the full suite to the spec's holdout ids plus merged
  ones is for the PRD owner.
- The nav's "Tasks" and "Review" entries (console shell): they open `/tasks`
  and `/tasks/review`, which still show unit 56's scaffold placeholders ("No
  data yet. The screen fills in later units"). The TRD routes only
  `/tasks/[taskId]` and `/tasks/[taskId]/review` and the UI context says there
  is no seventh screen, so no later unit filled them and none was invented on
  2026-10-25. Options for the PRD owner: the entries open the most recently
  active task's detail and review; or a task list becomes a named route.
- The Overview lists every "Needs a human" row (201 in the e2e fixture, an
  18,700 px page); the UI context names the table but no cap or scroll.
- A merged factory PR leaves its issue open (delivery): the PR body says
  "Factory PR for #N" with no closing keyword, and the PRD names neither the
  keyword nor who closes the issue, so none was added. On 2026-10-25 the
  eight merged scratch issues stayed open while their tasks read `merged`.
  Whether the body should carry `Closes #N` is for the PRD owner.
- Unexpected errors read as `blocked_reason: timeout` (runner): on 2026-10-25 a
  model-proxy 401 and a pre-run workspace failure both reached the issue as
  `timeout`. The PRD names no infrastructure reason, so none was invented;
  whether one should exist is for the PRD owner.
- Decided 2026-10-25 (unit 67): the install derivation is floored at the
  industry per-task cap of US$5; the median rule has no floor (PRD as
  written); an eval replay gets its own ceiling of items × the per-task
  budget; the installation read carries the last dry-run derivation. The
  three questions below are kept as the record of the choice.
- Derived budget versus a whole task (unit 52, structural, decided above): the install
  dry run replays the build slice only (`run-spec` on hand-written
  specs: build, code gates, commit; 0.38 a task on the seeded
  repository) and derives 2× the highest cost (0.76), while a full task
  also runs plan and review, and TRD §6.3 refuses a phase when spent +
  phase cap > budget; with the shipped caps (1.50 / 3.00 / 1.50) even the
  first phase of every task is refused after install. The offline gate
  tuned the second repository's caps to 0.20 / 0.40 / 0.20 (roster
  configuration). Options needing a decision: (A) the dry run replays
  the whole pipeline, as the PRD's "replaying 3 eval issues" reads, so
  the derivation measures a whole task (install takes three full runs);
  (B) a floor on the derived budget at the sum of the roster caps; (C) the
  check hands a phase the smaller of its cap and the remaining budget
  instead of refusing it. The recommendation is (A) with (B) as the floor.
  Related: the daily cap N × 3 × budget also bounds an eval replay, which
  runs its 20 to 50 items in one day on one scratch trace (24 items at
  about 0.55 each exceed 6.84), so the second installation's replay only
  fits with N = 10 in its `.factory/settings.yaml`; whether an eval run
  should count against the day's cap, or be spread over days, needs the
  same decision.
- Median budget floor (unit 49): the PRD sets the per-task budget to 2×
  the trailing median with no floor. A small early sample (one cheap
  docs task) can set a budget below the planner's phase cap, after which
  `check_budget` refuses every first phase until someone raises a task.
  Unit 49 applies the rule as written; a floor (for example never below
  the sum of the phase caps, or never below the install value for the
  first 30 days) needs a PRD decision.

Financial (decided 2026-10-24):

- Default model ids in `fixtures/factory_config/roster.yaml` stand: planner
  and builder `claude-opus-5-5`, reviewer `gpt-5-codex`.
- Model spend up to US$100 in total is authorised without asking; paid
  runs start once `app/.env` holds `ANTHROPIC_API_KEY` and `CODEX_API_KEY`
  (no `.env` exists yet). Spend is recorded per run in this tracker.

Structural (decided 2026-10-24): task containers and the LiteLLM proxy run
on Docker Desktop on this PC (Docker 29.8 installed); the pilot is the
seeded scratch repository pushed to GitHub; the phase 2 gate on GitHub runs
later while phase 3 is built. Earlier item, accepted:

- Phase 2 ships a provisional delivery step (push branch, open PR with a
  short body, `factory:review`) because the PRD's phase 2 gate reads
  "label → PR" while the TRD defers PR delivery to phase 3. Phase 3 replaces
  the body with the trace renderer and adds CI re-entry and the full suite.
  See `00-build-plan.md`.

Resolved in unit 31: an identical spec rewrite is dropped from the diff
check and logged (`replan: spec unchanged`); the runner's cause checks
(new comment, holdout present, spec blob changed) decide whether a resume
re-plans or stays blocked with a comment naming why.

Design (from the styleguide's gap list):

- SG3: whether a per-class eval drop alone blocks a change. Only the overall
  drop over 10 points blocks until decided.
- Theme override: the TRD says no toggle in v1 and also lists a best-effort
  `localStorage` theme override. Assumed: follow `prefers-color-scheme`, keep
  the override slot, ship no visible toggle.

Tooling decisions taken in the context files (the user said to continue;
treated as accepted unless objected to):

- Python: `uv`, `ruff`, `mypy --strict`, `pytest`.
- Console: CSS Modules over vendored Industry CSS, no Tailwind;
  `openapi-typescript` for API types; `zod` at boundaries; `vitest`.
- Repository layout in `architecture.md` System Boundaries.
- Console `InstallDryRun` (unit 63): the TRD lists the panel but no
  endpoint carries the dry run's derivation (the three issues, their
  costs, the 2× factor), which the installer writes to `install.log` on
  the data volume. The panel shows the budget facts the installation read
  carries and says where the record lives. Should the installation read
  (or a new field on it) carry the last derivation record?

## Architecture Decisions

- 2026-10-24 — The export signing key is Ed25519 through `cryptography`
  (already a dependency), not PyNaCl as the architecture doc lists.
  Reason: one fewer package for the same primitive; the architecture
  doc's tooling row is updated when the exporter lands (phase 4).
- 2026-10-24 — The PreToolUse hook bundle reaches Claude Code through
  `--settings` from a read-only directory (`deploy/hooks`, mounted at
  `/opt/factory/hooks`), not as a `.claude/settings.json` written into
  the worktree as the build plan worded it. Reason: bare mode skips
  `.claude/settings.json` auto-discovery (CLI reference, 2026-10-24),
  and a file in the worktree would be writable by the agent and visible
  to `git add -A`.
- 2026-10-24 — Task-container egress on Docker Desktop is an `--internal`
  Docker network that only the proxy joins; the nftables `DOCKER-USER`
  rule set and the denial tailer ship for the Linux VM. Reason: the
  user's choice; Docker Desktop's engine runs in its own VM with no host
  nftables chain, and an internal network gives the same single route
  out without depending on Docker Desktop internals.
- 2026-10-24 — Phase 3 runs task containers and the LiteLLM model proxy on
  Docker Desktop on the developer's Windows PC; the pilot repository is the
  seeded scratch repository; the phase 2 gate on GitHub runs after phase 3
  starts; model spend up to US$100 in total needs no further approval.
  Reason: the user's choices on 2026-10-24; fastest iteration, zero risk to
  real code, and the gate's plumbing is already proven offline.
- 2026-10-24 — Context files live in `app/context/`; the PRD, TRD and UI
  exports stay in `../specs/` as read-only inputs. Reason: `app/` is the
  factory repository root the agent works in; the specs are shared with the
  design package and should not be copied.
- 2026-10-24 — The PRD wins over every context file for behaviour; the TRD
  wins for structure. Reason: both are marked definitive and the context
  files are summaries.
- 2026-10-24 — Python tooling is `uv` + `ruff` + `mypy --strict` + `pytest`.
  Reason: the TRD fixes Python 3.12, pytest, Hypothesis and schemathesis but
  not the formatter, linter or package manager; one fast toolchain keeps the
  factory's own quality gates simple and mirrors what the factory will run
  on product repositories.
- 2026-10-24 — Console styling is the vendored Industry stylesheet plus a
  console token sheet and CSS Modules; Radix primitives unstyled; no
  Tailwind. Reason: the design package is plain CSS on tokens and the PRD
  requires an open-source stack with no component-library look.
- 2026-10-24 — Console API types are generated from the orchestrator's
  OpenAPI document; `zod` validates SSE rows and BFF input. Reason: TRD §6
  makes the pydantic models the single contract.
- 2026-10-24 — Repository layout fixed in `architecture.md` System
  Boundaries. Reason: the TRD names modules but not a tree; one tree lets
  unit specs name paths.
- 2026-10-24 — `migrations/` stays at the repository root (as documented)
  and is force-included into the wheel at `orchestrator/migrations/`;
  `TraceDB` resolves the repository copy first. Reason: the orchestrator
  applies migrations at start and runs from the repository in Compose, while
  a wheel install must still carry them.
- 2026-10-24 — The trace package splits `db.py` (connection, pragmas,
  migrations, transaction lock, read helpers) from `writer.py`
  (`TraceWriter`). Reason: readers in later units need the connection
  without the write surface.
- 2026-10-24 — Schema enums (`orchestrator/models/enums.py`) are the single
  spelling of every PRD state, reason, gate, kind and role; the CHECK
  constraints in `0001_init.sql` are the same strings.
- 2026-10-24 — Webhook and poll idempotency: delivery GUIDs cannot be
  reconstructed by the poller, so poll ids derive from the GitHub objects
  (`issue_event:<id>`, `pr_merged:<n>:<sha>`, `review:<id>`,
  `check_run:<id>`, `push:<sha>`). A webhook and a poll of one event may both
  be `github_events` rows; zero duplicate side effects rests on the state
  machine (one task per issue, approvals bound to the spec sha,
  status-guarded cancel and resume). This narrows the TRD §4.5 wording;
  the PRD guarantee is unchanged.
- 2026-10-24 — Each unit is committed on `feat/NN-unit-name` and
  fast-forwarded into `main`; no remote exists yet, so nothing is pushed.

## Session Notes

- 2026-10-24 — Sources read in full: PRD v3.0, TRD v1.0, Industry design
  system, the Console Styleguide text and tokens, and the four component
  exports. Not read: `Factory Console.dc.html` and `Console Journeys.dc.html`
  beyond a token grep; consult them when building the screens.
- Dark theme values come from the Console Styleguide, not from Industry
  (which ships light only).
- Toolchain on the development machine: Python 3.12.10, `uv` 0.13.5
  (installed with `pip install uv` into that Python), Node 24, git 2.55,
  Docker 29, `gh` 2.102. Global git `core.autocrlf=true`; the repository
  carries `.gitattributes` forcing LF so working copies match the files as
  written.
- The first commit was made while `main` was still unborn, so `main` was
  created afterwards from the feature branch; history is linear.
- No model call, GitHub App or VM has been used; every test runs offline.
- Operator keys live in `app/.env` (gitignored; template `.env.example`); the
  CLI loads it from the working directory or `FACTORY_ENV_FILE`. Shell
  variables win over the file.
- Claude Code CLI on this machine: not on PATH; the VS Code extension bundles
  `~/.vscode/extensions/anthropic.claude-code-2.1.309-win32-x64/resources/native-binary/claude.exe`.
  Set `FACTORY_CLAUDE_BIN` to it (or run `claude install`) before the phase 1
  gate's real run. `--bare` needs `ANTHROPIC_API_KEY` in the task env; the
  real run is a financial decision for the user.
- Shell heredocs in this environment unescape one backslash level: a patch
  string containing a backslash escape lands as the escaped character.
  Build such content with `chr()` or write the file with the Write tool.
- The `claude_*.jsonl` stream fixtures are hand-authored to the documented
  shapes; replace them with real recordings from the phase 1 gate run.
- `--system-prompt` is passed on argv; inside containers (phase 3) consider
  `--system-prompt-file` on a mounted path if prompts grow past the Windows
  or container argv limits.
- Commit `04dc018` (unit 03) was made with one Hypothesis test still failing
  because a shell pipe masked pytest's exit status; the next commit fixed the
  canonical reader (`parse_canonical`) and the check chain now uses
  `pipefail`. Verify the checklist from the command output, never from a
  piped summary.
- Commit `fbec693` (unit 05) was also made before its checks finished: the
  git commands sat on a line after a heredoc, outside the `&&` chain. The
  next commit fixed an unused import and mypy platform narrowing and
  recorded the full green run. The commit step now lives inside an explicit
  `if check; then` block.
