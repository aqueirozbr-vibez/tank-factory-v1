# Build Plan

Decomposition of PRD v3.0 phases 1 to 5 into units. Each unit produces one
visible, verifiable result, stays inside one system boundary from
`architecture.md`, and depends only on units above it. Units are built in
this order; a unit never needs a component a later PRD phase owns (TRD §1).

Dependencies are installed in the unit that first uses them. Every unit ends
with the checklist in `ai-workflow-rules.md` plus the verification lines here.
The `fake` harness is the default in every test; real CLIs run behind a flag;
no unit requires a model call, a GitHub App or a VM until its phase gate run.

Resolved ambiguity recorded here: the PRD's phase 2 gate reads "label → PR",
while the TRD's phase 2 row says PR delivery is not needed yet. This plan
ships a **provisional delivery** step at the end of phase 2 (push the branch,
open the PR with a short body, apply `factory:review`, under the App token)
and lets phase 3 replace the body with the trace renderer, add CI re-entry and
the full suite with the holdout. This is a structural call for the user to
confirm; see `progress-tracker.md`.

Development platform note: the production target is one Linux VM. Phases 1
and 2 are developed and tested on Windows and Linux (worktrees, subprocesses,
SQLite). Process-group termination is implemented per platform behind one
function. Phase 3 on (Docker, nftables, Compose) is tested on Linux only.

---

## Phase 1 — Build loop

Gate: 10 hand-written specs produce 10 green branches with trace rows and
cost; a seeded edit to `tests/acceptance/` is rolled back and fails the phase
10 of 10 times; the startup self-test rejects a placeholder command 100% of
the time.

### Unit 01 — Repository scaffold

- Boundary: repository root, `orchestrator/`, `cli/`, `tests/`.
- Builds: git repository; `pyproject.toml` managed by `uv` with the
  `orchestrator` and `cli` packages; `ruff`, `mypy --strict`, `pytest`
  configured; `orchestrator/models/settings.py` (`pydantic-settings`),
  `orchestrator/errors.py`; `.gitignore`; `tests/unit/test_scaffold.py`.
- Depends on: nothing.
- Verify: `uv run ruff format --check .`, `uv run ruff check .`,
  `uv run mypy .`, `uv run pytest` all pass; `uv run factory --version`
  prints the package version.

### Unit 02 — Trace schema and single writer

- Boundary: `orchestrator/trace/`, `migrations/`.
- Builds: `migrations/0001_init.sql` with the full TRD §4.2 schema (tables,
  CHECKs, triggers, indexes, views) minus `console.db`; `trace/db.py`
  opening one connection with WAL, foreign keys, busy timeout, applying
  migrations in order inside transactions and recording
  `schema_migrations`; `TraceWriter` with `insert_installation`,
  `load_config_version`, `insert_task`, `start_run`, `start_phase`,
  `finish_phase`, `finish_run`, `write_gate_result`, `write_envelope`,
  `start_process`, `end_process`.
- Depends on: 01.
- Verify: migration applies once and is idempotent on restart; the daily-cap
  trigger recomputes; `events` append-only triggers abort an update;
  `config_versions` rejects a same-vendor roster; `runs.status` defaults to
  `fail`; all under pytest with a temp database.

### Unit 03 — Events hash chain

- Boundary: `orchestrator/trace/`.
- Builds: `trace/chain.py` with RFC 8785 canonicalisation, `payload_hash`,
  genesis `prev_hash`, record hash, `verify(from_seq, to_seq)`;
  `TraceWriter.write_event` using it for every `kind`; a `purge` path that
  nulls `payload_json` and leaves the chain valid.
- Depends on: 02.
- Verify: Hypothesis property tests: any sequence of events verifies;
  changing any covered field breaks verification; nulling payloads does not.

### Unit 04 — Factory configuration models

- Boundary: `orchestrator/models/`, `orchestrator/config/`.
- Builds: pydantic models for `roster.yaml` (role → harness, model,
  thinking, tools, writes, prompts, caps, max_turns), protected paths,
  quality commands, budget settings; a loader from a local `.factory/`
  directory producing a `config_sha` and a `config_versions` row; the
  shipped default roster (planner and builder on `claude_code`, reviewer on
  `codex`) in `fixtures/factory_config/`.
- Depends on: 02.
- Verify: default roster loads; a same-vendor roster is refused before the
  database CHECK; `config_sha` is stable for the same tree.

### Unit 05 — Harness adapter contract and fake harness

- Boundary: `orchestrator/harness/`.
- Builds: `harness/base.py` (`HarnessRequest` with prompt, schema, cwd,
  env, max_turns, budget, timeout, resume session id; `HarnessResult` with
  envelope text, session id, usage, harness version, exit; a stream callback
  for `tool_call` rows); `harness/process.py` with per-platform
  process-group termination (SIGTERM then SIGKILL on POSIX; job object or
  `taskkill /T` on Windows); `harness/fake.py` replaying recorded envelopes
  and tool calls from `fixtures/envelopes/`.
- Depends on: 03.
- Verify: the fake harness yields an envelope, a session id, usage and
  `tool_call` events written to the trace in order; a timeout kills a
  sleeping child process tree on this platform.

### Unit 06 — Claude Code adapter

- Boundary: `orchestrator/harness/`.
- Builds: `harness/claude_code.py` building the argv for `claude -p` with
  `--output-format stream-json`, `--json-schema`, `--max-turns`,
  `--max-budget-usd`, the non-interactive permission mode, `--resume`;
  parsing stream-json into `tool_call` rows, final result, session id and
  usage; reading the harness version from the binary once per process.
- Depends on: 05.
- Verify: parser reproduces a recorded `fixtures/streams/claude_*.jsonl`
  into the expected rows; argv builder is exact; no network in tests.

### Unit 07 — Envelope models and the schema gate

- Boundary: `orchestrator/models/`, `orchestrator/gates/`.
- Builds: `models/envelopes.py` (`PlanOutput`, `BuildOutput`,
  `ReviewOutput`, `VerifyOutput`) with JSON schema export for the harness;
  `gates/envelope_schema.py` validating harness text into a model or a
  violations list; `envelopes` rows with `valid` and `violations_json`.
- Depends on: 05.
- Verify: valid and invalid fixtures for each model; the JSON schema passed
  to the harness equals the model's schema.

### Unit 08 — Permissions snapshot, diff and rollback

- Boundary: `orchestrator/gates/`.
- Builds: `gates/permissions.py`: snapshot a worktree before an agent call
  (tracked and untracked file hashes), diff after, match against the
  protected set and the role's write boundary, roll back violating paths
  (`git checkout --` and remove untracked), return a `GateResult` with every
  path checked; writes `gate_results(gate='permissions_enforce')` and an
  `incident` event on violation.
- Depends on: 03, 04.
- Verify: on a temp git repository a seeded edit under `tests/acceptance/`
  is detected, rolled back and the gate fails, 10 of 10 runs; an edit under
  `src/` passes; a planner writing outside `specs/` fails.

### Unit 09 — Claim gates

- Boundary: `orchestrator/gates/`.
- Builds: `gates/claims.py` with `artifacts_exist`, `files_non_empty`,
  `diff_matches_claims` (every `changed_files` entry appears in
  `git status`), each returning evidence lists; `gate_results` rows.
- Depends on: 07, 08.
- Verify: fixtures where a claimed file is missing, empty, or not in the
  diff fail the right gate with the path named.

### Unit 10 — Quality runner and startup self-test

- Boundary: `orchestrator/gates/`.
- Builds: `gates/quality.py` running `format`, `lint`, `typecheck`,
  `focused_tests` in order as subprocesses in the workspace, stopping at the
  first failure, capturing exit code and the last 4,000 characters, writing
  `gate_results`; `gates/selftest.py` running every configured command
  against `fixtures/known_failing/` and refusing start-up if any exits 0.
- Depends on: 04, 05.
- Verify: with scripted commands, order and short-circuit are exact; the
  output tail is capped at 4,000; a command that always exits 0 makes the
  self-test refuse 100% of the time across 20 runs.

### Unit 11 — Worktree workspace

- Boundary: `orchestrator/workspace/`.
- Builds: `workspace/worktree.py`: create branch `agent/<issue>` from the
  default branch and a worktree under the data directory; remove worktree;
  commit by code with the builder's message or a fallback; `git diff
  base...HEAD`.
- Depends on: 02.
- Verify: on a temp repository, create, commit and remove leave the branch in
  place and the worktree gone; the commit author is the factory identity.

### Unit 12 — Builder prompt and prompt builder

- Boundary: `prompts/builder/`, `orchestrator/prompt_builder.py`.
- Builds: `prompts/builder/system.md` and `user.md` with the byte-stable
  prefix order (guide; write boundary and command names; spec; previous
  `VerifyOutput`; reviewer findings); `prompt_builder.py` with the builder
  allow-list and a guide sha; nothing from `os.environ` or settings can be
  passed.
- Depends on: 04, 07.
- Verify: a snapshot test of the assembled prompt for one spec; passing a
  settings object or a dict with `token` in it raises; the 4,000-character
  tail cap is applied to the previous `VerifyOutput`.

### Unit 13 — Build phase state machine

- Boundary: `orchestrator/issue_to_pr.py`, `orchestrator/phases/build.py`.
- Builds: the phase 1 slice of the state machine: for a task with a spec
  path and a worktree, run `build` → `code_gates` → `commit` with the
  snapshot, the builder call, `envelope_schema`, `permissions_enforce`, the
  claim gates, the quality runner, and the repair loop (same session, cap 3,
  violation list plus output tail); the fourth failure marks the task
  `blocked` with `blocked_reason='repair_cap'` and an evidence record; a
  protected-path violation blocks at once with `protected_path`; API 5xx and
  rate limits retry 3 times without consuming repairs; `runs`, `phases`,
  `events`, `processes` written throughout.
- Depends on: 08, 09, 10, 11, 12.
- Verify: with the fake harness: green path commits and finishes `pass`; a
  failing-then-passing fixture uses one repair; a four-failure fixture blocks
  with `repair_cap`; the seeded protected edit blocks with `protected_path`;
  every transition has its `events` rows and the chain verifies.

### Unit 14 — Watchdogs and cost accounting

- Boundary: `orchestrator/watchdogs.py`, `orchestrator/budget/`.
- Builds: per-phase timeout (build and repair 120 min) killing the process
  group and failing the phase with `blocked_reason='timeout'`; phase cost
  and tokens from harness usage written to `phases` and the `agent_end`
  event; `tasks.spent_usd` and `runs.total_cost_usd` as sums; a per-task
  `budget_usd` check before each phase (phase 1: from settings) blocking
  with `budget`; the stuck-run flag at 15 min on `processes.last_event_at`.
- Depends on: 13.
- Verify: a fake harness that sleeps past a 2 s test timeout is killed and
  the phase fails with `timeout`; costs sum exactly; a budget of $0.01
  blocks before the phase starts.

### Unit 15 — Factory CLI, phase 1 commands

- Boundary: `cli/`.
- Builds: `factory --version` from the pinned tag; `factory dev seed <dir>`
  creating a scratch product repository with real `format`, `lint`,
  `typecheck` and `test` commands, 10 hand-written specs under `specs/`, a
  `.factory/` folder from the default fixture and the known-failing
  fixture; `factory run-spec --repo <dir> --issue N --spec <path>` running
  unit 13 end to end (phase 1 entry point on hand-written specs); `factory
  trace --task T [--phase P]` printing events; `factory verify --from --to`.
- Depends on: 13, 14.
- Verify: `factory dev seed` then `factory run-spec` with the fake harness
  produces a green branch and a verifiable chain; `factory trace` shows the
  tool calls and the cost.

### Unit 16 — Phase 1 gate run

- Boundary: `tests/integration/`.
- Builds: `tests/integration/test_phase1_gate.py` running the 10 seeded
  specs with the fake harness, the 10 seeded protected edits, and the
  self-test refusal 20 times; a documented manual run with the real Claude
  Code CLI behind `FACTORY_REAL_HARNESS=1` recorded in `progress-tracker.md`
  with its cost.
- Depends on: 15.
- Verify: 10 green branches with trace rows and cost; 10 of 10 rollbacks;
  100% self-test refusal; the real-harness run completed at least once on
  the operator's machine with the cost noted.

---

## Phase 2 — Spec and review

Gate: 10 real `risk:low` issues go from label to PR with 0 human events;
at least 7 of 10 merge; 1 deliberately ambiguous issue stops with exactly 1
question; both instruments published in the repository.

### Unit 17 — GitHub client

- Boundary: `orchestrator/github/`.
- Builds: App JWT, installation token minted per task (1 h, cached per task
  id); REST calls for issues, comments, labels (add, remove), collaborator
  permission, Contents, branches, PRs, request reviewers; `CODEOWNERS`
  parsing; every call records `label_write` or `comment_write` events with
  the identity used; recorded fixtures for tests.
- Depends on: 03.
- Verify: contract tests against recorded responses; a label write by an
  agent identity is impossible by construction (no function accepts a
  non-App token).

### Unit 18 — FastAPI application and webhook receiver

- Boundary: `orchestrator/api/`, `orchestrator/intake/`.
- Builds: the FastAPI app with `/healthz` and `POST /webhooks/github`:
  HMAC `X-Hub-Signature-256` and GitHub IP range check, `github_events`
  insert `ON CONFLICT DO NOTHING`, 202, nothing else in the request; the
  normalised event model; a daily refresh of the GitHub IP ranges.
- Depends on: 17.
- Verify: webhook fixtures (`labeled`, `unlabeled`, `pull_request merged`,
  `check_run`, `workflow_run`, `push`) route; a bad signature is 401; a
  duplicate delivery is one row.

### Unit 19 — Reconciliation poller

- Boundary: `orchestrator/intake/`.
- Builds: a 60 s poller over the issue timeline and PR APIs producing the
  same normalised events and ids as the webhook path; cursor persistence;
  `poll` events.
- Depends on: 18.
- Verify: a webhook and a poll of the same event produce one
  `github_events` row; the poller recovers a missed delivery.

### Unit 20 — Router, admission, queue and cancel

- Boundary: `orchestrator/intake/`, `orchestrator/issue_to_pr.py`.
- Builds: the router; the actor sensor (write access, else remove label and
  comment); idempotent admission (`tasks` row, branch, worktree, before any
  GitHub change); the queue scheduler (N slots, label order, queue comment);
  cancel on `factory:ready` removal (SIGTERM within 60 s, `cancelled`,
  branch kept, last-phase comment); `FACTORY_ENABLED` read before every
  phase.
- Depends on: 13, 17, 18.
- Verify: 100 repeated label events create 0 duplicate tasks; a label from a
  read-only user is removed with a comment; with N=1 the second task waits
  `queued` with a comment; cancel stops a running fake phase.

### Unit 21 — Issue template, holdout checkout and issue fetcher

- Boundary: `orchestrator/intake/`, `orchestrator/workspace/`,
  `fixtures/`.
- Builds: `.github/ISSUE_TEMPLATE/factory-task.yml` (installed by the CLI);
  the holdout checkout (clone of the holdout branch, `git fetch` before use,
  never inside a task workspace); the issue fetcher tagging every section
  with author login and `author_association`, trusting only OWNER, MEMBER
  and COLLABORATOR; Portuguese or English language detection for the
  blocked-question reply.
- Depends on: 17, 11.
- Verify: a fetched issue renders as tagged data with no system-prompt
  path; the holdout directory is never under a worktree path.

### Unit 22 — Planner prompt and plan phase

- Boundary: `prompts/planner/`, `orchestrator/phases/plan.py`,
  `orchestrator/prompt_builder.py`.
- Builds: planner guides with the prefix order (guide; spec template and
  gate rules; `CLAUDE.md`/`AGENTS.md`; ADR index; tagged issue as data); the
  planner allow-list; the plan phase writing `specs/<issue>-<slug>.md` only,
  with `permissions_enforce` restricted to `specs/`; 30 min timeout; one
  envelope re-prompt.
- Depends on: 13, 21.
- Verify: with the fake harness a spec file appears on the task branch; a
  write outside `specs/` rolls back and blocks.

### Unit 23 — Spec gate and open questions

- Boundary: `orchestrator/gates/`.
- Builds: `spec_sections`, `spec_ears`, `spec_ac_holdout_map` (every AC id
  mapped to at least one holdout test id that exists in the holdout
  checkout), `spec_adr_cited`; the correction loop (cap 2, same session);
  missing holdout blocks at once with `missing_holdout`; non-empty
  `open_questions` posts a comment in the issue's language, labels
  `factory:needs-human`, blocks with `open_questions`.
- Depends on: 22.
- Verify: fixtures for each failing rule; the ambiguous-issue fixture stops
  with exactly one question comment.

### Unit 24 — Risk classifier

- Boundary: `orchestrator/instruments/`, `fixtures/factory_config/`.
- Builds: `instruments/risk.py` over `.factory/risk.yaml` (tiers, size,
  blast radius, dependency multipliers, thresholds, governed by path); the
  breakdown object; writing the class to the spec header, `tasks.risk`,
  `tasks.risk_score` and the issue label; the shipped `risk.yaml`.
- Depends on: 23.
- Verify: the PRD's five risk worked examples to the decimal
  (40 low, 109.2 high, 30 low, 60 high, override recorded); tier U scores 0
  and blocks for spec correction.

### Unit 25 — Plan approval waiter

- Boundary: `orchestrator/phases/`, `orchestrator/intake/`.
- Builds: `risk:high` posts the spec comment and `factory:plan-review`,
  then waits for `factory:plan-approved` from a write-access actor with the
  current `spec_sha`; `risk:governed` requires a CODEOWNER of the governed
  path; `approvals` row from the label event (actor, event id, bound sha);
  a spec edit after approval invalidates it; a stale or invalid label is
  recorded and ignored with a comment.
- Depends on: 24.
- Verify: approval from a non-owner on a governed task is ignored; an
  approval predating a spec edit does not start the build; a valid approval
  starts it.

### Unit 26 — Codex adapter

- Boundary: `orchestrator/harness/`.
- Builds: `harness/codex.py` for `codex exec --json --sandbox read-only`
  with schema, resume, usage and version parsing; the adapter contract test
  suite runs against both adapters.
- Depends on: 06.
- Verify: parser reproduces a recorded `fixtures/streams/codex_*.jsonl`;
  the same contract tests pass for `fake`, `claude_code` and `codex`.

### Unit 27 — Reviewer prompt and review phase

- Boundary: `prompts/reviewer/`, `orchestrator/phases/review.py`,
  `orchestrator/gates/`.
- Builds: reviewer guides (guide; policy severities and examples; spec;
  builder envelope; gate evidence; diff); the reviewer allow-list; a fresh
  read-only session with tools Read, Glob, Grep; the orchestrator refuses a
  same-vendor roster at start; `verdict_consistent` gate with one re-prompt;
  45 min timeout; reviewer cost on its own phase row.
- Depends on: 13, 26.
- Verify: an envelope with `approved=true` and a blocking finding is
  re-prompted once then fails; every AC without a `met` verdict forces
  `revision_needed`.

### Unit 28 — Verdict scorer and revision loop

- Boundary: `orchestrator/instruments/`, `orchestrator/issue_to_pr.py`.
- Builds: `instruments/verdict.py` over `.factory/review-policy.yaml`
  (tiers, spec link, evidence, scope multipliers; strongest finding;
  conformance; Non-goals dismissal with the quoted line;
  `human_decision_needed` class); the revision loop (findings and diff only,
  cap 2); third `revision_needed` or any `human_decision_needed` labels
  `factory:needs-human` and posts findings; the shipped `review-policy.yaml`.
- Depends on: 27.
- Verify: the PRD's five review worked examples to the decimal (56 → 44
  revision; 6 → 94 accepted; 100 → 0 then human after two repairs; Non-goals
  → 0 dismissed; override recorded).

### Unit 29 — Comment templates

- Boundary: `orchestrator/templates/comments/`.
- Builds: Jinja2 templates for queue, plan review, blocked (with command,
  exit code, output tail), review findings, budget and cancel comments,
  rendered only from trace rows and the spec; posted under the App.
- Depends on: 17, 28.
- Verify: snapshot tests per template; no template accepts a raw agent
  string outside the named fields.

### Unit 30 — Provisional delivery

- Boundary: `orchestrator/phases/deliver.py`.
- Builds: after `accepted`, push `agent/<issue>` under the per-task App
  token, open the PR with a short provisional body (spec link, gate summary,
  cost), apply `factory:review`, set `pr_open`; a merge webhook sets
  `merged`; never a second PR for the same task.
- Depends on: 20, 28, 29.
- Verify: on the scratch repository the PR opens once; a repeated deliver
  call is a no-op; the merge event closes the task.

### Unit 31 — Blocked, resume and restart basics

- Boundary: `orchestrator/issue_to_pr.py`, `orchestrator/watchdogs.py`.
- Builds: resume on `factory:needs-human` removal per `blocked_reason`
  (open questions, missing holdout, spec gate and human decision re-enter
  plan and stay blocked unless the cause changed; repair, revision and
  timeout start a new attempt with fresh caps; protected path and dependency
  never resume); the start-up sweep closing orphaned `processes`; resume of
  running tasks at their active phase from the trace.
- Depends on: 30.
- Verify: each `blocked_reason` follows its rule in tests; a restart
  mid-plan re-runs the plan phase with attempt + 1 and no duplicate comment.

### Unit 32 — Phase 2 gate run

- Boundary: `tests/integration/`.
- Builds: the full loop on the scratch GitHub repository with the fake
  harness; the ambiguous-issue case; a documented real run of 10 `risk:low`
  issues on the pilot repository recorded with cost and merge count.
- Depends on: 31.
- Verify: 10 issues label → PR with 0 `engineer` events; at least 7 merged;
  exactly 1 question on the ambiguous issue; `risk.yaml` and
  `review-policy.yaml` published in the pilot repository.

---

## Phase 3 — Delivery and isolation

Gate: the builder fails 3 of 3 attempts to push, call `gh` or reach the
internet; an orchestrator restart mid-task opens 0 duplicate PRs across 5
restarts; the same workflow completes 5 issues on each harness; the
kill-switch stops a live task within 60 s.

### Unit 33 — PR body from the trace

- Boundary: `orchestrator/templates/`, `orchestrator/phases/deliver.py`.
- Builds: `pr_body.md.j2` rendered from `gate_results`, `phases`,
  `envelopes` and the spec: spec, gate table with pass or fail and evidence,
  cost in USD and tokens per phase, session ids, harness and model per role,
  advisory and dismissed findings with the Non-goals line; replaces the
  provisional body.
- Depends on: 30.
- Verify: snapshot of a rendered body; no field comes from agent free text
  except the named ones.

### Unit 34 — CI re-entry and merge observer

- Boundary: `orchestrator/intake/`, `orchestrator/issue_to_pr.py`.
- Builds: `workflow_run` and `check_run` failure and `changes_requested`
  re-enter the build phase on the same branch and PR within the same caps;
  `ci_wait` phase; merge sets `merged`; `pr_number` reused.
- Depends on: 33.
- Verify: a failing check_run fixture re-enters once and the PR number is
  unchanged.

### Unit 35 — LiteLLM model proxy and virtual keys

- Boundary: `orchestrator/budget/`, `deploy/`.
- Builds: the LiteLLM service config (vendor keys from env, two provider
  hosts); the admin client creating a per-phase virtual key with
  `max_budget` and `duration`, revoking at phase end, reading spend; 429 at
  the cap recorded as `budget_stop`; the task environment carries only the
  virtual key and the proxy base URL.
- Depends on: 14.
- Verify: integration test with LiteLLM in testcontainers: a key stops at
  its cap; the key is revoked at `phase_end`; no vendor key appears in the
  task environment.

### Unit 36 — Task image and container workspace

- Boundary: `orchestrator/workspace/`, `deploy/`, `cli/`.
- Builds: the per-installation image built at install from the product
  repository's `.devcontainer` or the factory base image plus the install
  command, rebuilt on `.factory/` or lockfile changes on `main`;
  `workspace/container.py` (create, exec, kill, rm) with the worktree
  bind-mounted, user `uid 10001`, an explicit environment, `docker kill`
  after 10 s grace; `workspace_kind='container'`.
- Depends on: 11, 35.
- Verify: a task runs the fake harness inside a container; the environment
  inside contains exactly the allowed variables; the container is removed
  when the task leaves `running`.

### Unit 37 — Egress enforcement

- Boundary: `deploy/`, `orchestrator/watchdogs.py`.
- Builds: the `factory-tasks` bridge with no default route; the nftables
  `DOCKER-USER` rule set (default DROP, allow proxy IP:4000, `nflog` prefix
  `factory-egress`); a journald tailer writing `egress_denied` events with
  the container id, destination and task.
- Depends on: 36.
- Verify: from inside a task container, GitHub, PyPI and the trace host are
  unreachable and each attempt is an `egress_denied` row; the proxy is
  reachable.

### Unit 38 — PreToolUse hook bundle

- Boundary: `orchestrator/workspace/`, `deploy/hooks/`.
- Builds: the hook scripts and the `.claude/settings.json` fragment placed
  in the task workspace denying `git push`, `gh `, `rm -rf`, `curl`, `wget`
  and any write to a protected path (exit 2 with the reason); the
  equivalent sandbox flags for Codex; hook denials visible as `tool_call`
  rows.
- Depends on: 36.
- Verify: each denied command is refused in a fake-harness replay that
  issues it; the post-run diff still catches a protected write.

### Unit 39 — Full suite with the holdout

- Boundary: `orchestrator/phases/full_suite.py`.
- Builds: `git fetch` of the holdout checkout; a fresh container from the
  task image with the holdout mounted read-only and no virtual key; the
  full suite on the final SHA; one counted builder repair on failure, then
  `blocked(full_suite)` with the failing tests in the comment.
- Depends on: 36, 21.
- Verify: a holdout-failing fixture repairs once then blocks; the builder
  container never had the holdout path mounted.

### Unit 40 — Kill-switch

- Boundary: `orchestrator/watchdogs.py`, `cli/`.
- Builds: `FACTORY_ENABLED` as the `installation.factory_enabled` flag read
  before every phase; setting it to 0 fans out SIGTERM to every live
  process and `docker kill` after 10 s, tasks become `cancelled`, branches
  stay; re-enabling is a confirmed control with a reason that re-admits
  nothing; `factory stop` and `factory start` CLI commands.
- Depends on: 36.
- Verify: with 3 fake phases running, `factory stop` ends all within 60 s
  and writes one `control` event plus three phase failures.

### Unit 41 — Budget service

- Boundary: `orchestrator/budget/`, `cli/`.
- Builds: the install-time 3-issue dry run deriving `per_task_budget_usd` at
  2× the highest observed cost (refuse to go live if fewer than 2 of 3
  complete), recorded in the install log; the daily cap trigger; the
  between-phase check against task and daily caps; the 10 s spend watchdog
  sending SIGTERM at the cap; `blocked(budget)` with `factory:budget` and a
  spend comment; a one-time raise of at most 2× with a reason and resume.
- Depends on: 35, 40.
- Verify: a fake phase with spend past the cap is stopped within 60 s; a
  second raise is refused; the daily cap holds the queue until the day
  rolls over in a clock-controlled test.

### Unit 42 — Harness-agnostic builder

- Boundary: `orchestrator/issue_to_pr.py`, `orchestrator/config/`.
- Builds: the roster selects the builder harness per role; the same
  workflow runs the builder on Claude Code or Codex with no code change;
  the two-vendor check at start; harness version on every phase row.
- Depends on: 26, 38.
- Verify: the phase 1 gate suite passes with the builder on each adapter
  (fake-recorded streams for both); a roster with Codex for both builder
  and reviewer is refused.

### Unit 43 — Governed PRs and branch protection

- Boundary: `orchestrator/phases/deliver.py`, `cli/`.
- Builds: a `risk:governed` PR opens with the governed path's CODEOWNER
  requested; `factory install` verifies branch protection requires
  CODEOWNER review and refuses to go live otherwise.
- Depends on: 25, 33.
- Verify: fixture PR for a governed task requests the right owner; an
  installation without the protection rule fails the install check.

### Unit 44 — Deployment bundle and installer

- Boundary: `deploy/`, `cli/`.
- Builds: `docker-compose.yml` (orchestrator, litellm, caddy; console slot
  reserved), `Caddyfile` with the internal and public listeners, the systemd
  unit, `factory install` (GitHub App settings, keys under `/etc/factory`,
  image build, migrations, self-test, dry run, install log with public key),
  `factory upgrade --pin vX.Y.Z` with the release procedure (pause
  admissions, drain 30 min, migrate, pull, up, self-test, eval slot, resume
  or roll back).
- Depends on: 36, 37, 41.
- Verify: a clean Linux VM reaches `/healthz` green after `factory install`
  with the fake harness; `factory upgrade` to the same tag is a no-op.

### Unit 45 — Restart reconciliation

- Boundary: `orchestrator/issue_to_pr.py`.
- Builds: start-up reconciliation reading GitHub (branch, PR, head sha,
  labels) before acting; adopting an existing PR by branch name; resuming
  `ci_wait` without a new PR; agent phases restart with attempt + 1 and
  unchanged caps.
- Depends on: 31, 34.
- Verify: 5 restarts mid-task open 0 duplicate PRs and 0 duplicate comments.

### Unit 46 — Phase 3 gate run

- Boundary: `tests/integration/`.
- Builds: the egress, push and `gh` refusal test; the 5-restart test; the
  5-issues-per-harness run; the kill-switch timing test; recorded on the
  pilot VM.
- Depends on: 45.
- Verify: the four phase 3 gate conditions, each with its evidence in
  `progress-tracker.md`.

---

## Phase 4 — Eval and KPIs

Gate: eval pass rate at least 60% overall on the pilot, per class; cost per
merged PR at most 2× median over 2 weeks; the second installation passes
its own 20-issue eval within 10 points of the pilot.

### Unit 47 — Eval runner

- Boundary: `orchestrator/eval/`, `cli/`.
- Builds: `eval_sets`, `eval_items`, `eval_runs`, `eval_results` use; the
  runner replaying a set on `eval/scratch` with pass rate overall and per
  class, baseline delta and `blocked` when the drop exceeds 10 points;
  triggers `install_dry_run`, `config_pr`, `factory_upgrade`,
  `harness_upgrade`, `manual`; the 24-issue fixture set in the class mix;
  `factory eval run`.
- Depends on: 45.
- Verify: the fixture set runs with the fake harness; a seeded regression
  of 13 points marks the run blocked.

### Unit 48 — KPI materialiser and audit queue

- Boundary: `orchestrator/trace/kpi.py`.
- Builds: `kpi_daily` for the five headline metrics and the leading
  indicators, nightly and on merge; the monthly audit sampler (every
  `risk:high` and `risk:governed` merge, Bernoulli 0.10 for `risk:low`) with
  `UNIQUE (task_id, month)`; `v_audit_queue`; no per-login grouping.
- Depends on: 03, 34.
- Verify: metric values against hand-computed fixtures; Hypothesis on the
  sampler's strata.

### Unit 49 — Budget switch to the rolling median

- Boundary: `orchestrator/budget/`.
- Builds: after the first week, `budget_mode='median'` with
  `per_task_budget_usd` = 2× the trailing 30-day median (or all tasks while
  fewer than 30 days exist), recomputed daily; the daily cap follows by
  trigger.
- Depends on: 41, 48.
- Verify: clock-controlled test switches on day 8 and recomputes.

### Unit 50 — Slack notifications

- Boundary: `orchestrator/notify/`.
- Builds: incoming-webhook notices for plan review waiting and stuck run;
  notification only.
- Depends on: 25, 14.
- Verify: payload snapshot; no code path treats a Slack response as an
  approval.

### Unit 51 — Retention, purge and signed export

- Boundary: `orchestrator/trace/`, `cli/`.
- Builds: the nightly retention job at 03:00 installation time nulling
  payloads older than `retention_days` and appending one `purge` event per
  day; metadata deletion at 365 days; `factory purge --task T` and `--login
  L` (pseudonymised display within 7 days); Ed25519 key at install;
  `GET /tasks/{id}/export` JSONL with the signed trailer; `factory export`.
- Depends on: 03.
- Verify: a purged chain still verifies; the export signature verifies with
  the public key from the install log.

### Unit 52 — Second installation and phase 4 gate run

- Boundary: `cli/`, `tests/integration/`.
- Builds: `reference_pass_rate` entry for the second installation; the
  install procedure repeated on the second in-house repository with its own
  class-mixed set.
- Depends on: 47, 49, 51.
- Verify: the three phase 4 gate conditions with evidence.

---

## Phase 5 — Console

Gate: the tech lead completes 10 plan approvals and 1 monthly audit cycle
from the console with every label under their own login; Overview at most
2 s p95 on the pilot's full history; a WCAG 2.2 AA audit with 0 critical
findings; the kill-switch stops a live task from the console within 60 s.

### Unit 53 — Read API and streams

- Boundary: `orchestrator/api/`.
- Builds: every read endpoint in TRD §6.1 (`/installation`, `/tasks`,
  `/tasks/{id}`, `/events`, `/envelopes`, `/gates`, `/review`,
  `/approvals/*`, `/audit/queue`, `/kpis`, `/indicators`,
  `/needs-human/count`, `/eval/*`, `/config`, `/config/history`); SSE
  `/tasks/{id}/events/stream` and `/changes`; cursor paging 200 rows; the
  standard error body; rate limits; the committed OpenAPI document and its
  contract test.
- Depends on: 48, 47.
- Verify: schemathesis green; a row appears on the stream within 2 s of its
  write in a test; Overview queries under 2 s with 1,000 seeded tasks.

### Unit 54 — Authorisation table

- Boundary: `authz.yaml`, `orchestrator/authz/`, `console/lib/authz.ts`.
- Builds: the decision table and the generator producing `authz.py` and
  `authz.ts`; the collaborator permission cache (5 min); fail-closed on
  unknown permission.
- Depends on: 17.
- Verify: every row in pytest and vitest; both generated files agree; the
  app refuses to start on an unparsable table.

### Unit 55 — Control endpoints

- Boundary: `orchestrator/api/`.
- Builds: `POST /tasks/{id}/budget`, `/resume`, `/risk-override`,
  `POST /audit/{id}`, `/controls/concurrency`, `/controls/kill-switch`,
  `/controls/admissions` (with `clear_incident`), `POST /eval/runs`, each
  with `Idempotency-Key`, the authz check, the bounds (raise once at most
  2×, N 1 to 10, kill-switch 1/min) and one `control` event.
- Depends on: 53, 54, 41.
- Verify: contract tests; a repeated idempotency key returns the first
  response; every control writes exactly one event with the login.

### Unit 56 — Console scaffold

- Boundary: `console/`.
- Builds: Next.js 15 App Router project with TypeScript strict; vendored
  `industry.css` and `console-tokens.css` (dark theme, `--sem-*`,
  `--font-mono`); `Shell`, `ThemeRoot`, `KeyboardMap`, `Freshness`; the
  `/unsupported` route and the under-1280 px redirect; `lib/api.ts` with
  generated `api.types.ts`; `/healthz`-backed status in the header.
- Depends on: 53.
- Verify: `npm run lint`, `typecheck`, `build` pass; the six routes render
  their titles with placeholder data; light and dark both render.

### Unit 57 — Sign-in and sessions

- Boundary: `console/lib/auth.ts`, `console/app/auth/*`,
  `console/app/api/console/*`.
- Builds: Auth.js with GitHub App user-to-server authorisation; `console.db`
  sessions (AES-GCM, 8 h); permission lookup; the BFF handler skeleton
  (validate, session, authz, act, error body); sign-out revokes the token.
- Depends on: 56, 54.
- Verify: sign-in flow against a test App; a read-only user sees no
  controls; the token never appears in a response body.

### Unit 58 — Primitive components

- Boundary: `console/components/`.
- Builds: `Chip`, `KPITile`, `Meter`, `ScoreBreakdown` ported from the
  design exports with every state in `ui-context.md`; vitest plus axe.
- Depends on: 56.
- Verify: each component's states render; axe reports 0 violations; chips
  expose "Task state: blocked".

### Unit 59 — Overview

- Boundary: `console/app/page.tsx`, `console/components/`.
- Builds: `KpiRow`, `NeedsHumanTable`, `RunningQueuedTable`, `SpendByTask`,
  `IndicatorsPanel`, `StuckFlag`, `NavBadge` (30 s poll); the kill-switch
  pill (dialog in unit 65).
- Depends on: 58, 53.
- Verify: Playwright against 1,000 seeded tasks loads under 2 s p95; the
  Indicators panel is collapsed by default.

### Unit 60 — Task detail and live trace

- Boundary: `console/app/tasks/[taskId]/`, `console/components/`.
- Builds: `TaskStrip`, `PhaseTimeline`, `GateLadder` with
  `EvidenceDrawer`, `RepairRounds`, `EnvelopeViewer`, `ChainFooter`, the
  task budget `Meter`, `ScoreBreakdown` (risk), `TraceTable` with the SSE
  tail (pause, resume by `seq`, 4 s poll fallback, "Tail behind" warning),
  `CopyJson`.
- Depends on: 59.
- Verify: 10,000 trace rows render under 2 s p95; a new row appears within
  5 s; `p` pauses the tail; gates after a failure are not clickable.

### Unit 61 — Review findings

- Boundary: `console/app/tasks/[taskId]/review/`.
- Builds: `ReviewHeader`, `AcTable`, `FindingsList`, `RoundSwitcher`,
  `ScoreBreakdown` (review).
- Depends on: 60.
- Verify: blocking, advisory and dismissed ordering; the Non-goals line is
  quoted on dismissed rows.

### Unit 62 — Approvals, audit and label writes

- Boundary: `console/app/approvals/`, `console/app/api/console/labels/`.
- Builds: `PlanQueue`, `SpecView` with the AC→holdout existence table,
  `ApprovalCard` writing `factory:plan-approved` under the user's token
  through the BFF (409 when the spec sha changed), `QuestionsTable`,
  `AuditStrata`, `AuditTable`, `AuditDecisionRow`, `RiskOverrideDialog`;
  cancel and resume as label removals.
- Depends on: 57, 61, 55.
- Verify: an approval appears on GitHub with the test user as actor and the
  `approvals` row binds to the spec sha; a non-CODEOWNER sees the governed
  button disabled with the reason.

### Unit 63 — Eval and versions

- Boundary: `console/app/eval/`.
- Builds: `EvalSetSummary`, `RunsByVersion` with `EvalComparisonTable`,
  `BlockedChange`, `InstallDryRun`, `RunEvalButton`.
- Depends on: 59, 47.
- Verify: a blocked run reads "blocked" with its delta in the fail role.

### Unit 64 — Configuration and change PRs

- Boundary: `console/app/config/`, `console/app/api/console/config-pr/`.
- Builds: `OpenChanges` with `ConfigChangeCard`, `RosterTable`,
  `InstrumentTables`, `BudgetsTable`, `ProtectedPathsTable`,
  `InstallationPanel`, `ProposeChangeDialog` opening a `.factory/` PR on
  branch `factory-config/<login>/<ts>` under the user's token; the `/changes`
  stream invalidating `config:` and `eval:` tags.
- Depends on: 62, 63.
- Verify: a proposed change appears as a PR under the test user and its
  eval result shows on the card; nothing is applied before merge.

### Unit 65 — Runtime controls and the kill-switch dialog

- Boundary: `console/components/`, `console/app/api/console/*`.
- Builds: `KillSwitchDialog` (Reason required, focus in Reason, Esc
  cancels, Enter confirms only when filled, per-pid progress), budget raise,
  concurrency, admissions pause and resume, incident clear, re-enable; each
  through the control endpoints with a confirmation and reason.
- Depends on: 59, 55.
- Verify: stopping from the console ends a live fake task within 60 s; every
  control shows as an `events` row with the login.

### Unit 66 — Phase 5 gate run

- Boundary: `tests/e2e/`, `tests/load/`.
- Builds: Playwright plus axe-core for the six screens' primary flows in
  light and dark and the keyboard map; k6 for Overview with 1,000 tasks,
  Task detail with 10,000 rows, SSE lag under 3 running tasks, 50 label
  events in a minute; the documented tech-lead run of 10 approvals and one
  audit cycle.
- Depends on: 64, 65.
- Verify: the four phase 5 gate conditions with evidence.

## Amendments after the plan

### Unit 67 — Budget decisions (2026-10-25)

- Boundary: `orchestrator/budget/dry_run.py`, `orchestrator/eval/runner.py`,
  `orchestrator/api/read.py`, `console/components/InstallDryRun.tsx`.
- Builds: the install derivation floored at the industry per-task cap (US$5);
  an eval replay's own ceiling (items × the per-task budget) instead of the
  day's cap; the last dry-run derivation on the installation read and in
  the console's panel. The median rule stays as the PRD writes it.
- Depends on: 44, 47, 52, 63.
- Verify: a cheap dry run derives US$5; a 24-item replay fits its own cap;
  the panel lists the three issues and their costs.
