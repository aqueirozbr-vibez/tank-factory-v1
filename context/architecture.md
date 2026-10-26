# Architecture Context

Source of truth: `docs/tank-factory-v1-trd.md` (TRD v1.0), which
traces every decision to `docs/tank-factory-v1-prd.md` (PRD v3.0).
This file is the short form the agent reads first. When they disagree, the TRD
wins for structure and the PRD wins for behaviour.

## Two Repositories

This repository (`app/`) is the **factory repository**: the orchestrator, the
console, the installer CLI, the prompts, the migrations and the deployment
bundle. It is installed per organisation at a pinned git tag.

The **product repository** is the customer's codebase on GitHub (pilot: an in-house
agentic SaaS repository). The factory never lives there. The product repository
keeps only a thin `.factory/` configuration folder plus the issue template, and
the factory reads and writes it through GitHub:

| Product repository path | Owner | Factory's relationship |
| --- | --- | --- |
| `.factory/roster.yaml` | Tech lead, by PR | Read on every `push` to `main`; defines role to harness, model, tools, write boundary, caps |
| `.factory/risk.yaml` | Tech lead, by PR | Risk classification instrument; read by code |
| `.factory/review-policy.yaml` | Tech lead, by PR | Review verdict instrument; read by code |
| `.factory/prompts/<role>/` | Tech lead, by PR | Per-installation prompt overrides |
| `.factory/` quality commands, protected paths | Tech lead, by PR | Gate commands and the protected set |
| `.github/ISSUE_TEMPLATE/factory-task.yml` | Installer commits once | Outcome, Scope, Non-goals, Acceptance criteria, Holdout |
| `specs/<issue>-<slug>.md` on `agent/<issue>` | Planner writes | The contract between human and factory |
| `docs/adr/` | Product team | Read by the planner; cited in Constraints |
| `tests/acceptance/` (holdout branch) | Product engineer | Never in a builder workspace; copied in for the full suite only |
| `tests/unit/`, `tests/integration/` | Builder may add | The only places the builder may create tests |
| `agent/<issue>` branches, PRs, labels, comments | Orchestrator under the App token | The visible state machine |

All `.factory/` paths, `.claude/`, `.github/workflows/`, `tests/acceptance/`,
`CODEOWNERS` and lockfiles are **protected paths** in the product repository.

## Stack

| Layer | Technology | Role |
| --- | --- | --- |
| Orchestrator | Python 3.12, FastAPI, pydantic v2, asyncio | One process: admission, state machine, phases, gates, caps, every GitHub write, REST API, SSE, poller, watchdogs, retention, KPI materialiser |
| Workflow engine | In-process state machine over SQLite (`tasks.status`, `phases`, `processes`) | Code owns sequencing; resume is a table scan at start. No Temporal, no LangGraph |
| Trace store | SQLite 3, WAL mode, `trace.db` | Single writer (orchestrator); `events` hash chain; the console's only data source through the API |
| Harness adapter | Claude Code CLI (`claude -p`) and Codex CLI (`codex exec`) as subprocesses with streamed JSON; `fake` harness replays recorded envelopes | One contract: prompt and schema in; envelope, session id, usage out; resume by session id; SIGTERM kills the process group |
| Model proxy | LiteLLM inside the installation | Holds vendor keys; issues per-phase virtual keys with `max_budget`; allow-lists at most 2 provider hosts; logs usage per key |
| Isolation | Git worktree per task (phases 1 and 2); Docker container per task from a per-installation image (phase 3 on) | Image built at install from the product repository's `.devcontainer` or the factory base image, rebuilt on `.factory/` or lockfile changes on `main` |
| Egress control | nftables on the host `DOCKER-USER` chain; bridge `factory-tasks` default DROP, allow only proxy IP:4000 | Denials logged with container id and written as `events(kind=egress_denied)` |
| Console | Next.js 15 App Router, React 19, TypeScript, Radix primitives, Lucide, Barlow + Barlow Condensed + JetBrains Mono | Six screens over the orchestrator API; SSE live tail; GitHub writes under the user's token |
| Console auth | Auth.js with GitHub App user-to-server authorisation; sessions in a separate `console.db` (SQLite), tokens AES-GCM encrypted, 8 h | A label written with a user token is attributed to the human |
| Console to orchestrator | REST, OpenAPI 3.1 generated from the orchestrator's pydantic models; SSE for `/events/stream` and `/changes` | Service token plus `X-Factory-Actor`; the console never opens `trace.db` |
| Authorisation | `authz.yaml` decision table generated into `authz.py` and `authz.ts` at build; GitHub collaborator permission cached 5 min | Default deny; fail closed when GitHub is unreachable |
| Edge | Caddy 2 | Internal 443 to console (internal CA); public 443 serves only `/webhooks/github` |
| GitHub integration | Custom GitHub App (Contents, Issues, Pull requests); webhooks with HMAC plus a 60 s reconciliation poller; GitHub Actions as the only CI | Idempotent on `(event_type, github_event_id)` |
| Templates | Jinja2 in `orchestrator/templates/` | Issue comments and the PR body, rendered from trace rows, never from agent free text |
| Secret scanning | gitleaks | CI on `prompts/` and `.factory/prompts/`; at ingestion on `tool_call.args`, `result_snippet` and envelope payloads with redaction |
| Export signing | Ed25519 via PyNaCl, key generated at install | Signed JSONL export per task |
| Packaging | Docker Compose (orchestrator, console, litellm, caddy) under one systemd unit; `factory` installer CLI in Python via `pipx`, pinned to a git tag | One VM, four services, one unit |
| Notifications | Slack incoming webhook (phase 4) | Notification only, never an approval |
| Observability | The trace database read by the console; optional OTLP export behind `FACTORY_OTLP_ENDPOINT` | OpenTelemetry GenAI attribute names on `agent_start`/`agent_end` |
| Python tooling | `uv` (env and lockfile), `ruff` (format and lint), `mypy --strict`, `pytest`, `hypothesis`, `schemathesis`, `testcontainers` | Context-file decision, not from the TRD; recorded in `progress-tracker.md` |
| Console tooling | `npm`, `eslint`, `tsc --noEmit`, `vitest`, Playwright + axe-core, k6 for load | Context-file decision, not from the TRD |

Fixed by the PRD and not re-opened: GitHub as the system of record, a Python
orchestrator as the only trace writer, two CLI harnesses, one Docker container
per task on one VM, English artefacts, an open-source UI stack.

## System Boundaries

Proposed layout of this repository. Folder names below are the names code and
specs must use; a unit spec that needs a new top-level folder updates this list
first.

- `orchestrator/` — the Python package. Owns every state transition, every
  gate, every GitHub mutation and every trace write. Nothing outside it opens
  `trace.db`.
  - `orchestrator/issue_to_pr.py` — the state machine: admission to merge, the
    repair, revision and correction caps, resume rules per `blocked_reason`.
    Phases do not transition themselves; this module does.
  - `orchestrator/api/` — FastAPI routers for `/api/v1`, the SSE streams, the
    `/webhooks/github` receiver and `/healthz`. Handlers validate, authorise,
    act through services, and return. They never run agents or long work.
  - `orchestrator/intake/` — webhook HMAC and IP check, the 60 s poller, the
    event normaliser (`github_events` idempotency), the router, admission, the
    queue scheduler, the issue fetcher with provenance tags, cancel and resume.
  - `orchestrator/phases/` — one module per phase: `plan`, `build`, `review`,
    `full_suite`, `deliver`, `ci_wait`. Each assembles inputs through the
    prompt builder, calls the harness adapter, and hands the envelope to gates.
  - `orchestrator/gates/` — pure gate functions returning evidence:
    `envelope_schema`, `artifacts_exist`, `files_non_empty`,
    `diff_matches_claims`, `permissions_enforce` (snapshot, diff, rollback),
    `spec_sections`, `spec_ears`, `spec_ac_holdout_map`, `spec_adr_cited`,
    `verdict_consistent`, and the quality runner (`format`, `lint`,
    `typecheck`, `focused_tests`, `full_suite`) with the startup self-test.
  - `orchestrator/instruments/` — `risk.py` and `verdict.py`: arithmetic over
    `.factory/risk.yaml` and `.factory/review-policy.yaml`. The PRD's worked
    examples are their unit fixtures.
  - `orchestrator/harness/` — the adapter contract, `claude_code.py`,
    `codex.py`, `fake.py`; version read from the binary; process-group kill.
  - `orchestrator/prompt_builder.py` — the only place a prompt is assembled,
    from an allow-list of fields per role.
  - `orchestrator/github/` — App JWT and per-task installation tokens, labels,
    comments, branches, PRs, Contents reads, collaborator permission lookups,
    CODEOWNERS parsing.
  - `orchestrator/trace/` — `db.py` (the single connection, pragmas,
    migrations, transaction lock), `writer.py` (`TraceWriter`), `chain.py` (RFC 8785 canonical JSON, SHA-256 chain, verify), `retention.py`
    (nightly job), `export.py` (signed JSONL), `kpi.py` (`kpi_daily`
    materialiser, audit sampler).
  - `orchestrator/budget/` — proxy admin client (create and revoke virtual
    keys, read spend), per-task and daily cap checks between phases, the 10 s
    spend watchdog, the install-time derivation and the median switch.
  - `orchestrator/workspace/` — `worktree.py`, `container.py` (Docker SDK:
    create, exec, kill, rm), image build and rebuild, holdout copy for the
    full-suite container.
  - `orchestrator/watchdogs.py` — phase timeouts, stuck-run flag (15 min), the
    kill-switch fan-out, the restart sweep.
  - `orchestrator/authz/` — `authz.py` generated from `/authz.yaml`; never
    hand-edited.
  - `orchestrator/clock.py`, `orchestrator/ids.py`, `orchestrator/constants.py`
    — UTC timestamps, row ids and every PRD number, each defined once.
  - `orchestrator/models/` — `enums.py` (the schema's CHECK strings as
    `StrEnum`s) and pydantic models: `PlanOutput`, `BuildOutput`,
    `ReviewOutput`, `VerifyOutput`, API request and response schemas, settings.
    The OpenAPI document is generated from here.
  - `orchestrator/templates/` — Jinja2: `comments/*.md.j2` (queue, plan
    review, blocked, review findings, budget) and `pr_body.md.j2`.
  - `orchestrator/notify/` — Slack webhook (phase 4).
- `console/` — the Next.js 15 application. Owns rendering, sessions, the BFF
  route handlers, and GitHub writes under the user's token. Owns no business
  rule and no trace write.
  - `console/app/` — routes: `/`, `/tasks/[taskId]`, `/tasks/[taskId]/review`,
    `/approvals`, `/eval`, `/config`, `/auth/*`, `/api/console/*`,
    `/unsupported`.
  - `console/components/` — server and client components named exactly as in
    `ui-context.md`.
  - `console/lib/` — `api.ts` (orchestrator client), `auth.ts` (Auth.js),
    `authz.ts` (generated), `github.ts` (user-token writes), `schemas.ts` (zod
    for SSE rows and BFF input).
  - `console/styles/` — `industry.css` (vendored from the design system),
    `console-tokens.css` (dark theme, `--sem-*`, mono face), CSS Modules per
    component.
- `cli/` — the `factory` installer CLI: `install`, `upgrade --pin`, `eval run`,
  `verify`, `purge`, `trace`, `export`, `dev seed`. Owns install-time steps
  (GitHub App setup, image build, dry run, budget derivation, self-test,
  install log) and never runs a task itself.
- `prompts/` — factory default guides: `planner/`, `builder/`, `reviewer/`,
  each with `system.md` and `user.md`. Byte-stable prefixes. Protected: a
  change ships only through a tag after an eval run.
- `migrations/` — forward-only SQL, `NNNN_short_name.sql`, one per PR, applied
  by the orchestrator at start. Existing files are never edited.
- `authz.yaml` — the authorisation decision table, source for both generated
  files.
- `deploy/` — `docker-compose.yml`, `Caddyfile`, LiteLLM config, the systemd
  unit, the nftables rule set, the task base image Dockerfile.
  - `deploy/product-repo/` — the files `factory install` commits into the
    product repository (the issue template under `.github/ISSUE_TEMPLATE/`).
- `fixtures/` — the PRD worked examples, the 24-issue eval set fixture,
  recorded harness envelopes for `fake`, the known-failing quality fixture,
  webhook payloads.
- `tests/` — `unit/`, `contract/`, `policy/`, `integration/`, `e2e/`, `load/`,
  mirroring the TRD's test levels.
- `context/` — these six files and `context/specs/` (the build plan and unit
  specs). The agent updates `progress-tracker.md`; humans own the rest unless
  a unit says otherwise.
- `docs/` — the PRD and TRD. Read-only inputs.

## Storage Model

- **`trace.db` (SQLite, WAL, on the data volume)**: the system of record for
  everything the factory did. Tables: `installation` (singleton),
  `schema_migrations`, `config_versions`, `user_permissions`, `tasks`, `runs`,
  `phases`, `events`, `envelopes`, `gate_results`, `processes`, `approvals`,
  `audit_decisions`, `github_events`, `eval_sets`, `eval_items`, `eval_runs`,
  `eval_results`, `kpi_daily`; views `v_needs_human`, `v_audit_queue`,
  `v_gate_fail_distribution`. `events`, `envelopes` and `gate_results` are
  append-only; retention nulls payload columns only. Caps on stored text:
  `gate_results.output_tail` 4,000 characters, `tool_call.result_snippet`
  1,000 characters.
- **`console.db` (SQLite, separate file)**: the console's `sessions` table
  only. The console is its only writer. Nothing else lives here.
- **Data volume files (`/var/lib/factory`)**: the holdout checkout (a clone of
  the product repository's holdout branch), task worktrees, the task image
  cache, the install log with budget derivation and public key.
- **`/etc/factory` (mode 0600)**: the GitHub App private key and the Ed25519
  export key, mounted read-only into the orchestrator.
- **Environment of each service**: webhook secret, LiteLLM master key, vendor
  keys (proxy only), console service token, Auth.js secret, Slack URL. Never
  in the trace, never in a prompt.
- **GitHub**: source of truth for code, specs, ADRs, `.factory/`
  configuration, labels, approvals (the label event), PRs, CI results. The
  factory mirrors what it needs into `config_versions`, `approvals` and
  `github_events`; it never treats its mirror as authoritative over GitHub.
- **LiteLLM's own log**: spend per virtual key, kept 1 year by the proxy.
- **Not used anywhere**: PostgreSQL, Redis, object storage, a vector store, a
  client-side state store, a shared central service.

Retention: payloads for `installation.retention_days` (30 to 365, default 90);
metadata for 1 year; `tasks` rows for the life of the installation; sessions 8
hours; permission cache 5 minutes; containers removed at task end; worktrees 7
days after `merged` or `cancelled`; branches kept.

## Auth and Access Model

Identities. There are exactly four, and each may do a fixed set of things:

| Identity | Holds | May | May never |
| --- | --- | --- | --- |
| Human (GitHub user, signed in to the console with a user-to-server token) | Session in `console.db` | Read per their repository permission; with `write`: approve `risk:high` plans, cancel, resume, raise one budget once (at most 2×), set N (1 to 10), pause and resume admissions, clear an incident, kill-switch off and on, record audits, propose `.factory/` PRs, run eval, export; as CODEOWNER of the governed path: approve `risk:governed` plans | Write to `trace.db`; approve through any channel other than the GitHub label |
| Orchestrator (GitHub App installation token, minted per task, 1 h) | App PEM, Docker socket, LiteLLM master key, `trace.db` | Push `agent/*` and `eval/scratch`; open PRs; write labels and comments; read Contents; create and revoke virtual keys | Push to `main`; approve or merge a PR; pass its token to any agent |
| Console service identity (service token plus `X-Factory-Actor`) | Service token | Read the API and `/changes` on behalf of a verified actor | Write without an actor; read `trace.db` directly |
| Agents `planner`, `builder`, `reviewer` (task container, `uid 10001`) | A phase-scoped LiteLLM virtual key only | Read the mounted worktree and the inputs listed in the agent model below; write inside the roster's `writes` boundary (planner `specs/`, builder everything except protected paths, reviewer nothing) | Reach GitHub, `trace.db`, the holdout checkout or any host but the model proxy; commit; push; run `gh` |

Rules:

- Readiness, plan approval, cancel, resume and merge are GitHub label or PR
  events whose actor is verified against the collaborator permission
  (`write`, `maintain` or `admin`; CODEOWNER for governed paths). The
  `approvals` row copies `actor_login` and `github_event_id` from the GitHub
  event. The console API has no endpoint that inserts an approval.
- An approval is bound to the spec file's sha (`approvals.bound_sha`); a spec
  edit after approval invalidates it and the task returns to plan review.
- Every API write passes the `authz.yaml` decision table first: `(permission,
  action, condition)` with default deny. Unknown permission plus GitHub
  unreachable is a deny.
- The console offers a control only when the table would allow it, and the
  orchestrator re-checks on every write.
- Runtime controls (budget raise, N, admissions pause, incident clear,
  kill-switch, re-enable) require a confirmation and a one-line reason and are
  recorded as `events(kind=control)` with the login. Standing configuration
  (roster, instruments, protected paths, quality commands, budget formula, N's
  default) changes only through a `.factory/` PR under the user's token, gated
  on the eval set.

## AI Agent Model

Three agents, each one bounded phase. No agent computes a score, decides a
verdict class, writes to GitHub or runs more than its cap.

| | Planner | Builder | Reviewer |
| --- | --- | --- | --- |
| Harness and vendor (shipped roster) | Claude Code, Anthropic frontier, thinking high | Claude Code, Anthropic frontier (may drop one tier from phase 4 after an eval shows at most 5 points drop) | Codex `exec --sandbox read-only`, OpenAI frontier; a different vendor from the builder, enforced by a CHECK on `config_versions` |
| Prompt prefix order | system guide; spec template and gate rules; `CLAUDE.md`/`AGENTS.md`; ADR index; provenance-tagged issue as data | system guide; write boundary and command names; spec; previous `VerifyOutput` (repair only, at most 4,000 chars); reviewer findings (revision only) | system guide; `review-policy.yaml` severities and examples; spec; builder envelope; gate evidence; `git diff base...HEAD` |
| May read | Repository read-only, `docs/adr/`, the tagged issue | The worktree inside the boundary, the command names, the spec | The worktree read-only, the spec, the diff, the builder envelope, gate evidence |
| May never read | Builder or reviewer outputs; any env beyond its key; the holdout checkout | The issue thread; planner or reviewer transcripts; `tests/acceptance/`; any GitHub credential | The builder's transcript; the issue thread; user identity; the holdout checkout |
| Output schema | `PlanOutput {status, summary, artifacts[], notes_for_next_agent, risk_inputs{tiers_touched[], est_lines, new_dependencies[], modules[]}, open_questions[]}` | `BuildOutput {status, summary, changed_files[], tests_added[], commit_message, notes_for_next_agent}` | `ReviewOutput {approved, verdict, findings[{requirement_id, met, severity, file, line, problem, impact, correction, evidence}], blocking[]}` |
| Gates after | `envelope_schema`, `permissions_enforce` (specs/ only), the four spec gates, risk recomputed in code | `envelope_schema`, `permissions_enforce`, three claim gates, `format`, `lint`, `typecheck`, `focused_tests` | `envelope_schema`, `verdict_consistent`, score and conformance recomputed in code, Non-goals dismissal by code |
| Caps | 1 call + 2 corrections; 30 min; phase cap $1.50 default | 1 build + 3 repairs per attempt; 120 min; `--max-turns 80`; $3.00 default | 1 call + 1 consistency re-prompt per round; 2 revision rounds; 45 min; $1.50 default |

Guides are files: defaults in `prompts/`, overrides in the product
repository's `.factory/prompts/`. Every `agent_start` event records the guide
sha, harness version, model id and virtual key id, so any prompt can be
reproduced from the trace.

## Background Work Model

- The orchestrator's asyncio loop owns all long-running work: the state
  machine per task, the poller (60 s), watchdogs (10 s spend check, phase
  timeouts, 15 min stuck flag), the retention job (03:00 installation time),
  the KPI materialiser (nightly and on merge), the monthly audit sampler, the
  eval runner, the restart sweep.
- Request handlers (FastAPI and Next.js) never start an agent, run a quality
  command or wait on GitHub. They write a row or enqueue a state change and
  return.
- Agents and quality commands run as subprocesses in a task container (or a
  worktree in phases 1 and 2), each with a `processes` row, a timeout and a
  process-group kill path: SIGTERM, 10 s grace, SIGKILL or `docker kill`.
- Infra retries (API 5xx, proxy 429) are 3 with backoff 5 s, 20 s, 60 s and
  never consume repair budget. Code failures do.

## Network

Five zones. Task containers egress default-DROP; the console is reachable only
on the internal interface; the public listener accepts one path.

| From | To | How |
| --- | --- | --- |
| Users on the organisation network | Caddy internal 443 | TLS from Caddy's internal CA; session cookie |
| GitHub | Caddy public 443, `/webhooks/github` only | TLS; HMAC `X-Hub-Signature-256`; source IP in GitHub hook ranges |
| Caddy | Console 3000, Orchestrator 8080 | Compose network |
| Console | Orchestrator 8080 | Service token plus `X-Factory-Actor` |
| Console | GitHub REST | User token; labels and `.factory/` PRs only |
| Orchestrator | GitHub REST and Git | App JWT to installation token, minted per task |
| Orchestrator | Docker daemon | Unix socket, never mounted into a task |
| Orchestrator | LiteLLM 4000 admin | Master key; create and revoke keys, read spend |
| Task container | LiteLLM 4000 | Virtual key; the only allowed destination (nftables) |
| Task container | Anything else | Dropped and logged as `egress_denied` |
| LiteLLM | `api.anthropic.com`, `api.openai.com` | Vendor keys; host allow-list of at most 2 |
| Orchestrator | Slack | Incoming webhook URL; phase 4 |

Everything the installation stores lives on the organisation's VM. There is no
multi-installation control plane in any infrastructure.

## Invariants

Rules the codebase must never violate. A unit that needs an exception stops and
raises it in `progress-tracker.md` first.

1. **One writer.** Only the orchestrator process opens `trace.db` for
   writing, through `orchestrator/trace/db.py`. The console reads through the
   API. No other module issues SQL against the trace.
2. **Append-only trace.** `events`, `envelopes` and `gate_results` are never
   updated or deleted in place. Retention nulls `payload_json`, `output_tail`
   and envelope payloads only; `payload_hash` and the chain survive.
3. **Code gates every agent output.** Nothing an agent emits reaches GitHub,
   a commit or the next phase until a deterministic gate has verified it and
   written a `gate_results` row with evidence.
4. **Instruments run in code.** Risk scores, review scores, conformance and
   verdict classes are computed by `orchestrator/instruments/` from the
   `.factory/` YAML. No agent computes a score; no prompt asks for one.
5. **Agents hold no credentials.** A task container's environment is built
   explicitly (`PATH`, `HOME`, base URL, one virtual key, `FACTORY_PHASE_ID`).
   No GitHub token, vendor key, master key or Docker socket ever enters it.
6. **Agents never touch GitHub.** Commit, push, PR, labels and comments are
   code steps under the App token minted per task. The builder never runs
   `git commit`; hooks deny `git push`, `gh`, `curl`, `wget`, `rm -rf`.
7. **Humans act under their own login.** Readiness, plan approval, cancel,
   resume, audits and `.factory/` PRs carry the human's GitHub login, through
   the GitHub label event or the user's own token. Nothing is approved under
   the App, and the console keeps no approval record of its own.
8. **The label is the approval.** `approvals` rows are derived from GitHub
   label events with the actor and event id; there is no API endpoint that
   inserts one; the approval is bound to the spec sha.
9. **Two vendors.** The builder and reviewer roster entries never share a
   vendor; a roster that violates this fails the `config_versions` CHECK and
   is not loaded.
10. **Protected paths are enforced, not advised.** `PreToolUse` hooks plus
    the post-run snapshot diff; a violation rolls back, blocks the task and
    opens an incident. It is never offered as a repair.
11. **The holdout stays outside.** `tests/acceptance/` is never mounted into
    a builder or reviewer container; it enters only the full-suite phase, in a
    fresh container, read-only, with no virtual key.
12. **One route out.** A task container reaches the model proxy on port 4000
    and nothing else; the rules live on the host, not in the container.
13. **Prompts come from the allow-list.** `prompt_builder.py` is the only
    prompt assembly path; it serialises named fields, never `os.environ`,
    headers, settings objects or the issue text into a system prompt.
14. **Success is earned.** `runs.status` and `phases.status` default to
    `fail`; only the state machine's `finish()` sets `success` or `pass`.
15. **Caps live in the state machine.** 3 repairs per build attempt, 2
    revisions per review, 2 spec-gate corrections, 1 envelope re-prompt, 1
    full-suite repair, `--max-turns 80`. They are constants in
    `issue_to_pr.py`, not sentences in prompts.
16. **One task per issue, one PR per task.** `tasks.task_id = owner/repo#n`
    is the idempotency key; re-entry reuses `tasks.pr_number`; restart
    reconciles GitHub before acting.
17. **Every control is an event.** Budget raise, N, admissions pause,
    incident clear, kill-switch, re-enable, audit decision and risk override
    each write one `events` row with login, reason, old and new value.
18. **Standing configuration changes only by PR.** Roster, instruments,
    protected paths, quality commands, the budget formula and N's default
    reach the orchestrator only as a new `config_sha` after a merge to `main`
    whose eval run passed. The console never applies them directly.
19. **Fail closed.** Unknown permission plus GitHub unreachable denies; a bad
    webhook signature or IP is discarded; proxy unreachable starts no agent
    phase; `FACTORY_ENABLED` unreadable is 0; a `config_versions` row that
    fails its checks is not loaded; a harness whose version cannot be read
    refuses to run; a placeholder quality command fails the startup
    self-test and the factory does not start.
20. **Handlers do not do long work.** No FastAPI or Next.js handler runs an
    agent, a quality command, a Docker build or a GitHub wait.
21. **Harnesses are subprocesses.** No in-process SDK for either harness;
    both are CLI processes with streamed JSON, a session id and a
    process-group kill.
22. **Forward-only migrations.** One `migrations/NNNN_*.sql` per PR, applied
    at start inside a transaction; existing files are never edited; previous
    versions must run on the new schema.
23. **No per-person metrics.** No view groups by login except the audit
    queue; no table stores line counts; no leaderboard, no tokens-per-task
    target, no velocity.
24. **The console is a window.** It writes GitHub labels under the user's
    token, `.factory/` PRs under the user's token, and control calls to the
    API. It never writes `trace.db`, the filesystem, code or a merge.
25. **Desktop only.** Viewports under 1280 px go to `/unsupported`; no route
    has a mobile layout.
26. **Phase order is a wall.** A phase's work never requires a component the
    next phase owns (TRD §1 "must not need yet" column).
