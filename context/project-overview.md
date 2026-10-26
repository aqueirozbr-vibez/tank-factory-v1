# Tank Factory v1

Source of truth: `docs/tank-factory-v1-prd.md` (v3.0, definitive)
and `docs/tank-factory-v1-trd.md` (v1.0), in this repository's `docs/`
folder. This file is the short form. When this file and the PRD
disagree, the PRD wins and this file gets corrected.

## Overview

Tank Factory v1 turns a GitHub issue that a human has
marked ready into a reviewed pull request with no human in between. A planner
agent writes the spec, a builder agent implements it inside deterministic code
gates, an independent reviewer agent on a different vendor's model checks the
diff against the spec, and code runs the full test suite, opens the PR and
attaches the evidence and the cost. It is built for teams of 1 to 10 engineers
building agentic SaaS: the operator's own product teams first, client engineering
teams from the same release. It installs from a version-pinned factory
repository onto one repository of one GitHub organisation, runs its agents on
Claude Code or Codex through one adapter, and ships a desktop web console where
the team watches every task's evidence and cost, approves plans and operates
the controls. One principle governs every design decision: code owns the
control flow, and agents own one bounded phase each. A human always merges.

## Guiding Principles

Use these to resolve any requirement the specs leave ambiguous.

1. Code owns the control flow; agents own one bounded phase each. Sequencing,
   retries, acceptance, budgets and every GitHub mutation are code, never an
   agent decision.
2. Every agent claim is verified by code. "Tests pass" is a gate result row,
   never a sentence in a transcript.
3. The agent never edits its own grader. Protected paths and holdout tests are
   enforced by `PreToolUse` hooks and the post-run diff. `CLAUDE.md` is
   advice; hooks and diffs are enforcement.
4. GitHub is the visible state store. Readiness, plan approval, review,
   blocked and merged are labels and PRs. Every human action is recorded
   under that human's own GitHub login; agents act only under the GitHub App.
5. One trace ledger. Every tool call is a row in the SQLite trace database.
   The PR body, the KPIs and the console are all generated from it; none of
   them is written by an agent.
6. Humans decide what the instruments say humans decide. Risk classification
   and review verdict policy are published YAML files under protected paths,
   recalibrated monthly, never changed silently.
7. Minimal means one repository, one installation, one orchestrator process,
   one builder per task. When in doubt, do not add infrastructure.
8. Two vendors, always. The builder and the reviewer never run on the same
   model vendor; the orchestrator refuses to start a task if they do.

## Users

- **Product engineer**: writes issues from the template, writes the holdout
  acceptance tests for each issue, grants readiness, answers a blocked task's
  question, reviews and merges factory PRs.
- **Tech lead**: approves `risk:high` and `risk:governed` plans, audits every
  high-risk and governed PR plus a 10% sample of low-risk PRs monthly, owns
  the risk and review instruments. Named backup for the operator.
- **Factory operator**: one named platform engineer who installs the factory,
  maintains roster, prompts and gates, watches traces, sets budgets and runs
  the eval set.
- **Client engineering lead**: receives a fully self-hosted installation on
  their own GitHub organisation and VM, chooses harness and model vendors,
  owns the GitHub App, reads per-installation KPIs.

## Goals

Measured 90 days after the pilot installation goes live, on the pilot
repository. The console is judged by its own phase gate.

1. At least 30% of PRs merged in a 30-day window come from `agent/*` branches.
2. At least 70% of PRs opened by the factory are merged.
3. At least 80% of `factory:ready` tasks reach `pr_open` or `blocked` with zero
   human events between admission and that state.
4. At most 5% of the 10% monthly sample of approved `risk:low` PRs are rejected
   by the tech lead on audit. Every `risk:high` and `risk:governed` PR is
   audited and its disagreement reported separately, not pooled.
5. Cost per merged PR is at most 2 times the rolling 30-day median, and zero
   tasks exceed their budget.
6. Zero protected-path changes reach a PR.
7. A second installation passes its own 20-issue eval set within 10 points of
   the pilot's pass rate.
8. The tech lead completes 10 plan approvals and one monthly audit cycle from
   the console, with every label written under their own login.

## Core User Flow

1. A product engineer opens a GitHub issue using the factory issue template.
   It has five headings: Outcome, Scope, Non-goals, Acceptance criteria and
   Holdout.
2. The product engineer writes holdout acceptance tests for each acceptance
   criterion into the holdout checkout (a separate checkout the builder never
   sees) and lists their test ids under Holdout.
3. A user with write access on the repository adds the label `factory:ready`.
4. The factory checks the label actor has write access. If not, it removes the
   label and comments with the rule. If yes, it admits the issue once:
   it creates the `tasks` row with `task_id = repo#issue`, the branch
   `agent/<issue>` from the default branch, and an isolated workspace (a git
   worktree in phases 1 and 2, a Docker container on the installation's VM
   from phase 3). If N tasks are already running (default 3), the task waits
   in `queued` and a comment says so.
5. One script fetches the issue body and comments, tags every section with the
   author's login and `author_association`, and passes them to agents as data.
   Issue text never goes into a system prompt.
6. The planner (Claude Code by default) reads the tagged issue, `CLAUDE.md`
   or `AGENTS.md`, `docs/adr/` and the repository, and writes
   `specs/<issue>-<slug>.md`. It may write only under `specs/`; any other
   path in the diff rolls back and blocks the task.
7. The spec gate (code) checks the seven sections (Outcome, Scope, Non-goals,
   Acceptance criteria, Constraints, Open questions, Test plan), at least one
   numbered criterion in `WHEN ... THE SYSTEM SHALL ...` form, every criterion
   mapped to an existing holdout test id, and at least one ADR cited or "no
   ADR applies". A failure re-prompts the planner with the violations, at
   most twice, then blocks. A missing holdout test blocks immediately with
   `factory:needs-human` because it is a human omission.
8. If the planner returns open questions, they are posted as an issue comment,
   the task is labelled `factory:needs-human`, and it stops. A write-access
   user answers in the thread and removes the label; the task re-enters the
   plan phase with the answers.
9. Code classifies risk from the spec's Scope paths and the planner's
   `risk_inputs` using `.factory/risk.yaml`, and writes the class to the spec
   header, the `tasks` row and an issue label.
   - `risk:low`: the build starts unattended.
   - `risk:high`: the spec is posted as a comment, the task is labelled
     `factory:plan-review`, and the build waits until a write-access user
     adds `factory:plan-approved`. The approval stores the login and the
     spec file's sha; a later spec edit invalidates it.
   - `risk:governed`: same label, counted only when its actor is a
     CODEOWNER of the governed path.
10. The builder (Claude Code by default) receives only the spec and the names
    of the test, lint and typecheck commands. It implements test-first inside
    its write boundary: every path except `.factory/`, `.claude/`,
    `.github/workflows/`, `tests/acceptance/`, `CODEOWNERS` and lockfiles;
    new tests only under `tests/unit/` and `tests/integration/`. Hooks deny
    `git push`, `gh`, `rm -rf`, `curl`, `wget` and protected-path writes.
11. After every builder call, claim gates run: `artifacts_exist`,
    `files_non_empty`, `diff_matches_claims`. Then quality gates run in order:
    format, lint, typecheck, the focused tests named in the Test plan. Any
    failure re-prompts the same builder session with the violation list, at
    most 3 repair rounds. The fourth failure blocks the task with
    `factory:needs-human` and posts the failing command, exit code and last
    4,000 characters of output. A protected-path change in the diff rolls
    back and blocks at once, with no repair offered.
12. Code commits with the builder's `commit_message` or a generated fallback.
    The builder never runs `git commit`.
13. The reviewer (Codex by default, `--sandbox read-only`, fresh session,
    tools `Read`, `Glob`, `Grep` only) receives the spec, the diff, the
    builder's envelope and the gate evidence. It returns one `met` verdict
    with `file:line` or test-name evidence per acceptance criterion, plus
    findings. Code computes the verdict from the findings using
    `.factory/review-policy.yaml`.
    - `accepted`: proceed to the full suite.
    - `revision_needed`: the findings only, with the diff, go back to the
      builder for a repair round, at most 2 rounds.
    - `human_decision_needed`, or a third `revision_needed`: the task is
      labelled `factory:needs-human` and the findings are posted.
14. Code copies the holdout acceptance tests into the workspace for this phase
    only and runs the full suite on the final SHA. A failure starts one more
    builder repair round within the cap, then blocks.
15. Code mints a per-task GitHub App token, pushes `agent/<issue>`, and opens
    the PR. The PR body is generated from the trace database: the spec, a gate
    table with every gate's pass or fail and evidence, cost in USD and tokens
    per phase, session ids, and harness and model per role. The label
    `factory:review` is applied. A `risk:governed` PR requests the CODEOWNER
    as reviewer, and branch protection requires that review before merge.
16. The product engineer, and the tech lead for high-risk and governed work,
    review the PR on GitHub. `changes_requested` or a GitHub Actions failure
    re-enters the build phase on the same branch and PR within the same repair
    cap. A second PR for the same task is never opened.
17. A human merges the PR. The merge webhook sets `tasks.status = merged`.
18. At any point the team opens the Factory console to watch the task's phase
    timeline, gate ladder, repair rounds, cost per phase and live trace tail.
    A plan approval, cancel or resume taken in the console is written as a
    GitHub label under the user's own login. Removing `factory:ready` or
    setting `FACTORY_ENABLED=0` stops the live agent process within 60 s.

## Features

### Intake and readiness

- `factory:ready` from a write-access user admits an issue; from any other
  actor the label is removed and a comment names the rule.
- Idempotent admission on `task_id = repo#issue`: a repeated label on a
  `queued`, `running`, `blocked` or `pr_open` task creates nothing.
- Task row, `agent/<issue>` branch and workspace are created before any agent
  runs and before any GitHub object changes.
- Provenance-tagged issue fetch by one script; trusted associations are
  OWNER, MEMBER and COLLABORATOR only.
- Issue template with Outcome, Scope, Non-goals, Acceptance criteria and
  Holdout headings.
- Concurrency cap N per installation (default 3, configurable 1 to 10);
  excess tasks wait in `queued` in label order.
- Admission around the clock; spend is bounded by caps, not by a schedule.
- Cancel by removing `factory:ready` or setting `FACTORY_ENABLED=0`: SIGTERM
  within 60 s, task `cancelled`, branch kept, comment names the last phase.
- GitHub intake through signed webhooks plus a 60 s reconciliation poller, so
  admission works behind a firewall.

### Spec and plan

- Planner writes `specs/<issue>-<slug>.md` only; any other path fails the
  phase and rolls back.
- Planner envelope validated against a JSON schema: `status`, `summary`,
  `artifacts[]`, `notes_for_next_agent`, `risk_inputs {tiers_touched[],
  est_lines, new_dependencies[], modules[]}`, `open_questions[]`. One
  re-prompt on parse failure, then block.
- Spec gate in code: seven sections, EARS-form criteria, every criterion
  mapped to an existing holdout test, ADR cited. At most 2 corrections.
- Open questions post a comment, label `factory:needs-human` and stop.
- Plan phase timeout 30 min; partial spec attached to the block comment.
- The planner never sees builder or reviewer output; the builder never sees
  the issue thread.

### Task risk classification

- Risk is the strongest change class the Scope touches, not the sum. Tiers:
  A Governed 100, B Data and contracts 70, C Application logic 40, D Leaf
  change 20, E Docs and config 10, U Unclassifiable 0.
- Multipliers for size (1.0 / 1.3 / 1.6), blast radius (1.0 / 1.2 / 1.5) and
  new dependency (1.0 / 1.5).
- Thresholds: `risk:low` below 50, `risk:high` at 50 and above, `risk:governed`
  by path or event (tier A path, destructive migration, new secret, budget
  request above 2× default). Score 0 blocks for spec correction.
- Instrument lives in `.factory/risk.yaml` under a protected path; tech-lead
  overrides are recorded in `approvals` and feed monthly recalibration.

### Bounded build

- Builder inputs limited to the spec, command names, and on repair the
  previous `VerifyOutput` envelope with the last 4,000 characters of output.
- Write boundary enforced by `PreToolUse` hook deny list and post-run diff.
- Harness invoked non-interactively with the role's tool allow-list,
  `--max-turns` (builder 80), per-phase budget and output schema.
- Claim gates after every call; quality gates in order: format, lint,
  typecheck, focused tests. Full suite is not run here.
- Startup self-test runs every configured quality command against a
  known-failing fixture and refuses to start if any command exits 0.
- At most 3 repair rounds per session; API 5xx and rate-limit errors retry up
  to 3 times with backoff and do not consume repair budget.
- Code commits; the agent never commits.
- Build and repair timeout 120 min; process tree killed on expiry.
- A change to `pyproject.toml`, `package.json` or a lockfile blocks with
  `factory:needs-human` until a human installs the dependency.

### Harness adapter

- One contract over Claude Code (`claude -p`) and Codex (`codex exec`) as CLI
  subprocesses with streamed JSON: prompt and schema in; envelope, session id
  and token usage out; resume by session id; SIGTERM on timeout.
- Roster in `.factory/roster.yaml` maps each role to harness, model,
  thinking, tools, write boundary, prompts and caps.
- Shipped roster: planner and builder on Claude Code, reviewer on Codex. An
  installation may swap either vendor but must keep two across builder and
  reviewer.
- Harness version recorded on every phase row.

### Independent review

- Fresh session, read-only tools, `writes: []`, different vendor from the
  builder.
- Inputs: spec, `git diff <base>...HEAD`, builder envelope, gate evidence.
  Never the builder's transcript.
- Envelope: `approved`, `verdict`, `findings[] {requirement_id, met, severity,
  file, line, problem, impact, correction, evidence}`, `blocking[]`.
- `verdict_consistent` gate rejects contradictory envelopes and re-prompts
  once.
- Every acceptance criterion needs a `met` verdict with evidence; a missing
  verdict forces `revision_needed`.
- Test integrity checks: a test with no assertion or asserting a constant is
  Major; a branch special-casing test inputs is a Blocker.
- Findings on Non-goals items are dismissed with the Non-goals line quoted.
- Review timeout 45 min; reviewer cost recorded on its own phase row.

### Review verdict policy

- Severity tiers: A Blocker 100, B Major 70, C Minor 30, D Nit 10,
  U Unverifiable 0 (no `file:line` or line not in the diff).
- Multipliers for spec link (1.0 / 0.9 / 0.5), evidence (1.0 / 0.8 / 0.4) and
  scope (1.0 inside Scope, 0 if in Non-goals).
- Blocking score is the strongest adjusted finding; conformance is 100 minus
  that. `accepted` at conformance 70 and above; `revision_needed` below 70;
  `human_decision_needed` is a class, not a score.
- Policy lives in `.factory/review-policy.yaml` under a protected path.

### Delivery

- Full suite including holdout tests runs by code on the final SHA before any
  PR.
- Push, PR, labels and comments by code with a per-task GitHub App token
  (Contents, Issues, Pull requests only); the token never reaches an agent.
- PR body generated from the trace database, never written by an agent.
- `factory:review` on PR open; merge webhook closes the task; CI failure or
  `changes_requested` re-enters the build on the same PR.
- GitHub Actions is the only CI integration, via `workflow_run` and
  `check_run` webhooks.
- Governed PRs open with the CODEOWNER requested and branch protection
  requiring that review.

### Trace, budgets and kill-switch

- SQLite trace in WAL mode with tables `tasks`, `runs`, `phases`, `events`,
  `envelopes`, `gate_results`, `processes`, `approvals`. The orchestrator is
  the only writer; the console reads through the orchestrator's API.
- One `events` row per tool call, written while the agent streams; one
  `agent_end` row per phase with tokens and cost. `events` are hash-chained
  and exportable as signed JSON per task.
- Retention: full trace for a configured 30 to 365 days (default 90), then
  metadata for 1 year.
- Per-phase cap passed to the harness; per-task `budget_usd` set at install
  from a 3-issue dry run at 2× the highest observed cost, never below the
  industry per-task cap of US$5 (user decision 2026-10-25, spec 67), then 2× the trailing
  30-day median after the first week; daily cap of N × 3 × per-task budget.
- Any cap breach sends SIGTERM within 60 s, blocks with `factory:budget` and
  posts the spend. A write-access user may raise one task's budget once, by
  at most 2×, with a reason.
- `FACTORY_ENABLED=0` stops every live process within 60 s; checked before
  every phase. Re-enabling is a confirmed control with a reason.
- Restart: every task resumes at its active stage from the trace and GitHub
  state; a startup sweep closes orphaned `processes` rows; zero duplicate
  PRs or comments.
- Resume from `blocked` by removing `factory:needs-human`, with re-entry rules
  per block cause (plan phase for questions, holdout and human decisions; new
  attempt for repair cap, full suite and timeout; budget raise for budget;
  never for protected path or dependency).
- Stuck-run flag after 15 min with no event, shown on the console and
  optionally posted to Slack.

### KPIs and eval set

- KPI SQL views refreshed daily, identical across installations: cost per
  merged PR, review minutes per PR, rework rate, escalation rate, merge rate,
  gate-fail distribution, human-audit disagreement, unattended rate.
- Monthly audit queue view: every approved `risk:high` and `risk:governed` PR
  plus a 10% random sample of `risk:low`, with disagreement per stratum.
- Eval set of 20 to 50 closed historical issues in a fixed mix of bugs, tests,
  docs and small features mirroring the last 90 days of merged PRs, replayed
  on a scratch branch, pass rate overall and per class.
- Every change to prompts, roster, gates, instruments, harness version or
  factory version runs the eval set; a drop over 10 points blocks the change.

### Factory console

- Six screens at 1440 px reference width, usable from 1280 to 1920 px:
  Overview, Task detail, Approvals, Review findings, Eval and versions,
  Configuration. No seventh screen; leading indicators are a collapsed panel
  on Overview.
- Every value read from the trace database or GitHub. The console writes only
  GitHub labels (approve, cancel, resume) under the user's own login,
  `.factory/` pull requests, and `events` rows for control actions.
- GitHub sign-in; controls offered only to users with write access; governed
  approval offered only to the path's CODEOWNER.
- Runtime controls (budget raise, N within 1 to 10, pause and resume
  admissions, clear incident, kill-switch, re-enable) require confirmation and
  a one-line reason and are recorded as `events`.
- Standing configuration (roster, both instruments, protected paths, quality
  commands, budget formula, N default) changes only through a `.factory/` PR
  gated on the eval set.
- Live trace tail shows a new row at most 5 s after it is written.
- Overview at most 2 s p95 with 1,000 tasks; Task detail at most 2 s p95 with
  10,000 trace rows.
- WCAG 2.2 AA; keyboard-only approvals and kill-switch; status, risk, gate and
  severity distinguishable without colour; light and dark themes.
- Open-source UI components and typefaces only; minimalist, instrument-like
  style; no brand tokens.

### Installation and portability

- Separate factory repository holding orchestrator, harness adapters, gate
  library, trace and installer; installed per organisation at a pinned
  version. The product repository keeps only its thin `.factory/`
  configuration.
- One installation per organisation: own GitHub App, VM, trace database and
  vendor keys; no shared control plane.
- Per-installation model proxy holds the vendor keys, issues phase-scoped
  budget-capped keys, and is the only egress from a task container; reaches
  at most 2 allow-listed vendor hosts.
- Per-installation container image built at install from the repository's
  devcontainer or the factory base image, rebuilt on `.factory/` or lockfile
  changes.
- Secret scanner on every PR at 0 findings.
- Issues accepted in Portuguese or English; specs, prompts, PR bodies and KPIs
  in English; a blocked task's question posted in the issue's language.
- LGPD applies to Brazilian installations; trace deletion on request within
  7 days.

## Scope

### In Scope

Five phases, each shippable, each adding one capability on the previous
phase's workflow and trace database.

- **Phase 1, Build loop**: factory repository with installer and version pin;
  `issue_to_pr.py` with the build phase, claim and quality gates,
  protected-path diff, SQLite trace, git worktree per task, real quality
  commands, startup self-test. Capability 3 on hand-written specs.
- **Phase 2, Spec and review**: intake on `factory:ready`; issue template with
  Holdout section and the holdout checkout; planner; spec gate with the
  criterion-to-holdout check; risk classification; plan approval; first cut
  of the harness adapter, enough to run the reviewer on Codex read-only;
  review verdict policy; repair caps; blocked-with-evidence. Capabilities 1,
  2 and 4.
- **Phase 3, Delivery and isolation**: GitHub App token flow; PR body from the
  trace; GitHub Actions re-entry on the same PR; install-time budget from a
  3-issue dry run; Docker container per task with egress limited to the model
  proxy; holdout in a separate checkout; `PreToolUse` hooks; kill-switch;
  harness adapter completed so the builder runs on either harness; CODEOWNER
  review on governed PRs through branch protection. Capability 5 except KPIs
  and eval.
- **Phase 4, Eval and KPIs**: eval set of 20 to 50 historical issues replayed
  on a scratch branch with pass rate per class; KPI views; budget switched to
  2× median; Slack notification for plan reviews (notification only); second
  installation on a second in-house repository, with a client repository
  following under a pilot agreement once the eval gap is within 10 points.
- **Phase 5, Console**: the six console screens over the trace database and
  GitHub; GitHub sign-in with write-access checks; approvals as labels under
  the user's login; control actions as events; configuration changes as
  `.factory/` PRs; light and dark themes. Capability 6.

### Out of Scope

Do not build, suggest, or add dependencies for any of the following in the
first release.

Product boundaries:

- A multi-agent orchestration platform or swarm runtime.
- A hosted, multi-tenant SaaS, or any hosted control plane for client
  installations. Client installations are self-hosted on client
  infrastructure.
- An IDE, chat assistant or prompt-to-app generator. The console has no chat
  with the agents and no code editor.
- Auto-merge under any score or verdict. A human merges every PR.
- A replacement for CI or for GitHub's PR review. The factory integrates with
  them; it does not re-implement them.
- A general observability product. KPIs are SQL views on the trace, nothing
  more.
- Jira, Linear, ServiceNow or any issue-tracker intake other than GitHub
  issues.
- CI providers other than GitHub Actions.

Task and agent boundaries:

- Multi-repository tasks. One repository per installation, one PR per task.
- Epics: the planner never splits one issue into several tasks.
- Issues from forks are never admitted.
- Parallel subagents writing code. One builder per task.
- A triage agent before phase 4 is complete. Until then only template-complete
  issues are admitted.
- Vector memory, cross-task memory or self-improving prompts.
- In-process SDK integration for either harness. Both are driven as CLI
  subprocesses only.
- A shared central model gateway. Model calls leave each installation only
  through its own proxy.
- Agents committing, pushing, calling `gh`, or holding a vendor credential or
  a GitHub token.

Infrastructure boundaries:

- Kubernetes, leases, or a Go control plane.
- MicroVM sandboxes (E2B, Daytona or similar). The isolation unit is a Docker
  container on the installation's VM.
- Admission SLA outside the installation's operating hours (best effort only).

Console boundaries:

- A mobile or tablet layout. Desktop only, 1280 to 1920 px.
- A multi-installation view. One console per installation.
- Proprietary typefaces, icon sets or UI libraries, or brand tokens.
- Any approval record held by the console itself. GitHub labels are the only
  approval record; Slack is notification only.
- Direct application of standing configuration. Roster, instruments,
  protected paths, quality commands and budget formula change only by PR.

Metrics that are deliberately not measured:

- Lines of code per agent, PRs per engineer as a leaderboard, tokens per task
  as a target, or agent velocity in story points.

Decisions deferred until after the first release (assume the first option):

- Commercial model for client installations (support retainer, licence, or
  bundled into an engagement).
- Triage agent timing (first post-v1 release, or after a second installation
  has run its eval set for a month).
- Which repository is the phase-4 second installation (the busier in-house
  repository, or a smaller third one first).

## Success Criteria

Each phase is done when its gate passes. The release is done when all five
gates pass and the 90-day goals are being measured on the pilot.

Phase 1, Build loop:

1. 10 hand-written specs produce 10 green branches, each with trace rows and a
   recorded cost.
2. A seeded edit to `tests/acceptance/` is rolled back and fails the phase 10
   times out of 10.
3. The startup self-test rejects a placeholder quality command 100% of the
   time.

Phase 2, Spec and review:

4. 10 real `risk:low` issues go from `factory:ready` to an open PR with zero
   human events in between.
5. At least 7 of those 10 PRs are merged.
6. One deliberately ambiguous issue stops with exactly one question posted and
   the `factory:needs-human` label applied.
7. `.factory/risk.yaml` and `.factory/review-policy.yaml` are published in the
   pilot repository under protected paths.

Phase 3, Delivery and isolation:

8. The builder fails 3 of 3 attempts to push, call `gh`, or reach the internet
   from inside its container.
9. An orchestrator restart mid-task opens zero duplicate PRs or comments
   across 5 restarts.
10. The same workflow completes 5 issues with the builder on Claude Code and 5
    with the builder on Codex, with no code change between runs.
11. The kill-switch stops a live task within 60 s.
12. A PR body shows the spec, the full gate table with evidence, cost per
    phase, session ids, and harness and model per role, all generated from the
    trace database.

Phase 4, Eval and KPIs:

13. Eval pass rate is at least 60% overall on the pilot, reported per class.
14. Cost per merged PR stays at or under 2× the rolling median for 2 weeks.
15. A second installation passes its own 20-issue eval set within 10 points of
    the pilot's pass rate.
16. A change that drops the eval pass rate by more than 10 points is blocked
    from merging.

Phase 5, Console:

17. The tech lead completes 10 plan approvals and one monthly audit cycle from
    the console, with every label written under their own GitHub login.
18. Overview loads in at most 2 s at p95 on the pilot's full history; Task
    detail loads in at most 2 s at p95 with 10,000 trace rows.
19. A WCAG 2.2 AA audit reports zero critical findings, and every approval and
    the kill-switch work by keyboard alone.
20. The kill-switch stops a live task from the console within 60 s.

Standing conditions, true at every phase:

21. Zero protected-path changes ever reach a PR.
22. Zero tasks exceed their budget without a recorded human raise.
23. Every task has `runs`, `phases`, `events`, `envelopes`, `gate_results` and
    `approvals` rows and exports as JSON.
24. No PR, label or comment is ever written under a human's token by an agent,
    and no approval is ever written under the GitHub App's identity.
