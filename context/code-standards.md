# Code Standards

Direct rules. Every unit of this build must look like one developer wrote it.
Stack, folders and invariants come from `architecture.md`; tokens and
component names come from `ui-context.md`. Tooling choices here that the TRD
did not fix (uv, ruff, mypy, CSS Modules, openapi-typescript, zod) are
context-file decisions recorded in `progress-tracker.md`.

## General

- Keep modules small and single-purpose. A module that owns a state
  transition does not also render a comment or call GitHub.
- Fix root causes. Do not add a retry, a sleep, a `try/except: pass` or a
  default value to make a failing path go quiet.
- Never mix the orchestrator, the console and the CLI in one change unless
  the unit spec says the change is a shared contract (OpenAPI, `authz.yaml`).
- Use the PRD's names verbatim everywhere a name appears: labels
  (`factory:ready`, `factory:plan-approved`, `factory:needs-human`,
  `factory:budget`, `factory:review`, `factory:plan-review`), statuses
  (`queued`, `running`, `blocked`, `pr_open`, `merged`, `cancelled`), risk
  classes (`risk:low`, `risk:high`, `risk:governed`), verdicts (`accepted`,
  `revision_needed`, `human_decision_needed`), gate names, severity tiers
  (`Blocker`, `Major`, `Minor`, `Nit`, `Unverifiable`), `blocked_reason`
  values and event kinds. Do not invent synonyms.
- Numbers from the PRD are constants with the PRD's name, defined once:
  repair cap 3, revision cap 2, spec corrections 2, envelope re-prompt 1,
  full-suite repair 1, max turns 80, timeouts 30/120/45 min, stuck 15 min,
  kill-switch 60 s, output tail 4,000 chars, result snippet 1,000 chars,
  session 8 h, permission cache 5 min, poll 60 s, N default 3, N range 1 to
  10, retention 30 to 365 default 90, conformance threshold 70, risk
  threshold 50, eval block 10 points, budget raise at most 2× once.
- No placeholder logic. A gate, command or check that always passes is a
  defect, not a stub. If a unit cannot implement a check yet, it raises
  `NotImplementedError` with the unit number and the spec marks it.
- No secrets in code, fixtures, tests, logs, prompts or the trace. Fixture
  tokens are obviously fake (`ghs_FAKE...`).
- Comments explain why, not what. Reference the PRD or TRD section when a
  rule is implemented ("PRD §Capability 3: fourth failure blocks").

## Python (orchestrator, cli)

- Python 3.12. `uv` manages the environment and `uv.lock`. Run everything as
  `uv run <tool>`.
- `ruff format` and `ruff check` with the repository config; zero warnings.
  `mypy --strict`; no `# type: ignore` without a trailing reason and an
  issue or unit reference.
- Full type hints on every function. Use built-in generics (`list[str]`,
  `dict[str, int]`, `X | None`). No `Any` in models or public signatures.
- pydantic v2 for every I/O boundary: harness envelopes (`PlanOutput`,
  `BuildOutput`, `ReviewOutput`, `VerifyOutput`), API request and response
  bodies, settings, webhook payloads, `.factory/` YAML. Frozen dataclasses for
  internal values that never cross a boundary.
- Parse harness output with `Model.model_validate_json`. A validation failure
  writes an `envelopes` row with `valid = 0` and `violations_json`; it never
  raises past the phase.
- One `Settings` class (`pydantic-settings`) in `orchestrator/models/
  settings.py` reads the environment once. No `os.environ[...]` anywhere else.
- Subprocesses: `asyncio.create_subprocess_exec` with an explicit argv list,
  `start_new_session=True`, an explicit `env` dict, and a `cwd`. Never
  `shell=True`. Kill the process group, never just the pid. Record every
  spawn in `processes`.
- Blocking work (git, Docker SDK, file copies) runs in `asyncio.to_thread` or
  as a subprocess, never on the event loop.
- Time: `datetime.now(timezone.utc)`; stored as ISO 8601 text with offset;
  display conversion happens in the console only.
- SQLite: one connection owned by `orchestrator/trace/db.py`; `PRAGMA
  journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=5000`. All writes go
  through `TraceWriter` methods named after the row they write
  (`write_event`, `write_gate_result`, `finish_phase`). Parameterised queries
  only. One transaction per state transition. No SQL strings outside
  `orchestrator/trace/` and `migrations/`.
- The hash chain uses one canonicaliser (`trace/chain.py`, RFC 8785) for
  both write and verify. Never build canonical JSON by hand.
- Gates are pure functions `(GateContext) -> GateResult` in
  `orchestrator/gates/`. A failed check is a result, not an exception.
  `checks_json` lists every item checked with `ok` and `note`.
- Instruments (`instruments/risk.py`, `instruments/verdict.py`) take the
  parsed YAML and the inputs and return a breakdown object (base, factors,
  score, class) that the console's `ScoreBreakdown` renders unchanged. The
  PRD's ten worked examples are exact-value unit tests.
- Domain exceptions live in `orchestrator/errors.py` and carry a `code`
  string. The API maps them to `{code, message, detail?, traceId}`. Messages
  are declarative and name the fix.
- Logging is diagnostics, not the record: standard `logging` with a JSON
  formatter; never log a token, a key, an envelope payload or issue text. The
  trace is the record.
- Jinja2 templates render only values that came from trace rows or the spec
  file. Agent free text appears only in the fields the template names
  (`summary`, `commit_message`, findings), never as raw template input.
- Every GitHub call goes through `orchestrator/github/`; callers pass the
  task id and the function mints or reuses the per-task installation token.
  No module builds an `Authorization` header itself.
- Prompt assembly happens only in `prompt_builder.py`, from the role's
  allow-list. A new field in a prompt is a change to that allow-list and to
  the role's row in `architecture.md`.

## TypeScript (console)

- `strict: true`, `noUncheckedIndexedAccess: true`,
  `exactOptionalPropertyTypes: true`. No `any`; use `unknown` and narrow.
- API types are generated from the orchestrator's OpenAPI document with
  `openapi-typescript` into `console/lib/api.types.ts`; never hand-written.
  Regenerate in the same PR as any API change.
- `zod` schemas in `console/lib/schemas.ts` validate everything that enters
  from outside the type system: SSE rows, BFF request bodies, GitHub
  responses, `localStorage` reads.
- Shared types live in `console/lib/types.ts`; component props are declared
  next to the component as `type XProps`.
- Named exports only. One component per file, file named after the
  component (`GateLadder.tsx`). Hooks in `console/lib/hooks/` prefixed `use`.
- No `console.log` in committed code; errors surface in the UI's error state
  or are thrown.

## Next.js

- Server components by default. Add `'use client'` only to the client islands
  `ui-context.md` lists (`TraceTable`, `GateLadder` drawer, `ScoreBreakdown`,
  `Meter`, `KPITile`, `Chip`, `KillSwitchDialog`, `ApprovalCard`,
  `RiskOverrideDialog`, `AuditDecisionRow`, `RoundSwitcher`,
  `EvalComparisonTable`, `RunEvalButton`, `ProposeChangeDialog`,
  `ConfigChangeCard`, `ThemeRoot`, `KeyboardMap`, `NavBadge`, `StuckFlag`,
  `EvidenceDrawer`, `CopyJson`).
- Data is fetched in server components through `console/lib/api.ts`. Live
  data (tasks, events, approvals, KPIs) uses `cache: 'no-store'`;
  configuration and eval reads are tagged `config:<config_sha>` and
  `eval:<run_id>` and revalidated from the `/changes` stream; GitHub Contents
  reads for `/config` are cached 60 s per `main` sha.
- No client-side store (no Redux, Zustand, React Query). Screen state lives in
  the URL (`?tab=`, `?round=`, `?attempt=`). Client memory holds only the
  tail-paused flag, open drawer, collapsed panel and theme override;
  `localStorage` holds the theme override and collapsed panel, best effort,
  every read and write in `try/catch`.
- The SSE client resumes from its last `seq`; falls back to a 4 s poll when
  the stream fails; shows "Tail behind by N s" when lag exceeds 5 s.
- Route handlers under `app/api/console/` are the BFF. Each does, in order:
  parse and validate the body with zod; load the session; evaluate
  `authz.ts`; act (proxy to the orchestrator with the service token and
  `X-Factory-Actor`, or write to GitHub with the user's token); return the
  standard error body on failure. Nothing else.
- Auth.js configuration in `console/lib/auth.ts` only. User tokens are
  encrypted at rest and never sent to the browser.
- Viewport under 1280 px redirects to `/unsupported` in `middleware.ts` or
  the root layout; no component implements a mobile branch.
- Nothing is statically pre-rendered except `/unsupported` and the keyboard
  map.

## Styling

- Two global stylesheets, loaded in the root layout in this order:
  `console/styles/industry.css` (vendored, never edited) and
  `console/styles/console-tokens.css` (the dark theme values, the five
  `--sem-*` roles, `--font-mono`).
- Component styles are CSS Modules next to the component. Every colour,
  font, spacing, radius and shadow is a `var(--*)` from `ui-context.md`. No
  hex, no font name, no raw px for a value a token carries. The only raw
  numbers allowed are the layout dimensions `ui-context.md` states (row
  heights, chip height, meter height, reference width).
- No Tailwind, no CSS-in-JS, no component library styles. Radix primitives
  are used unstyled for Dialog, Tabs, Tooltip and DropdownMenu behaviour.
- Status, risk, gate, verdict and severity colours come only from
  `--sem-pass`, `--sem-fail`, `--sem-warn`, `--sem-info`, `--sem-neutral`,
  always paired with the glyph and the text value from `ui-context.md`.
  Never use the accent for a state and never use a `--sem-*` role for
  decoration.
- Muted text is `color-mix(in srgb, var(--color-text) 62%, transparent)`;
  do not introduce a second muted value.
- Focus is `:focus-visible { outline: 2px solid var(--color-accent);
  outline-offset: 2px; }` from the design system. Never remove it, never
  restyle it per component.
- Respect `prefers-reduced-motion`: the tail tint and running pulse are the
  only animations, and both stop under it.

## API Routes (orchestrator)

- Order inside every handler: parse and validate the body (pydantic);
  identify the actor (`X-Factory-Actor` only when the service token is
  present; the HMAC-verified webhook otherwise); evaluate the `authz.py`
  decision table; check `Idempotency-Key` for writes; act through a service;
  write the `events` row; respond.
- Every write endpoint accepts `Idempotency-Key` and returns the same
  response for a repeated key.
- Response shapes come from the pydantic models; lists are `{items, next}`
  with a cursor; errors are `{code, message, detail?, traceId}` with the
  status codes the TRD catalogue names (401, 403, 404, 409, 422, 503).
- Rate limits from the TRD are enforced in FastAPI middleware with an
  in-memory token bucket per login; a limited request returns 429 with the
  rule named.
- `/webhooks/github` verifies HMAC and source IP before parsing, inserts into
  `github_events` with `ON CONFLICT DO NOTHING`, returns 202, and does
  nothing else in the request.
- SSE endpoints send `event: row` per insert and `event: ping` every 15 s,
  honour `after=<seq>`, and close on sign-out.
- The OpenAPI document is generated from the models and committed; a
  contract test fails when the committed document and the running app differ.

## Data and Storage

- Operational facts go in `trace.db` through `TraceWriter`. Nothing else
  writes there.
- Console sessions go in `console.db` and nowhere else.
- Large text is capped at write time: `output_tail` 4,000 characters,
  `result_snippet` 1,000 characters. Full command output is never stored.
- GitHub is authoritative for labels, PRs and configuration; the trace mirrors
  them with the event id that proved them. Never update a mirror column
  without the GitHub event that justifies it.
- Secrets never reach the database, a fixture or a log. gitleaks runs at
  ingestion on `tool_call.args`, `result_snippet` and envelope payloads with
  redaction; a hit opens an incident.
- Derived numbers (`kpi_daily`) are materialised by the job, never computed
  in a request.
- Migrations are forward-only files `migrations/NNNN_short_name.sql`, one per
  PR, additive so the previous version runs on the new schema. The schema in
  the TRD §4.2 is the reference; a migration that diverges from it updates
  `architecture.md` in the same PR.

## Testing

- Test layout mirrors the TRD levels: `tests/unit/`, `tests/contract/`,
  `tests/policy/`, `tests/integration/`, `tests/e2e/`, `tests/load/`.
- Unit tests for the instruments assert the PRD's worked examples to the
  decimal: 40 → `risk:low`; 109.2 → `risk:high`; 30 → `risk:low`; 60 →
  `risk:high`; 56 → conformance 44 → `revision_needed`; 6 → 94 → `accepted`;
  100 → 0; Non-goals → 0 dismissed.
- Hypothesis for the hash chain and the audit sampler. schemathesis against
  every `/api/v1` endpoint. The `authz.yaml` table is tested row by row in
  pytest and the same cases in vitest for `authz.ts`.
- Integration tests run with `harness=fake` by default and the real CLIs
  behind a flag, against a scratch GitHub repository and testcontainers.
- Playwright plus axe-core covers the six screens' primary flows in light and
  dark; 0 critical, 0 serious.
- A test that asserts nothing, asserts a constant, or special-cases its
  input is a defect (the factory's own reviewer treats these as Major or
  Blocker; hold this code to the same rule).
- Never change a test to make it pass unless the unit spec says the expected
  behaviour changed.

## File Organization

- `orchestrator/<area>/` — one area per folder as in `architecture.md`;
  `__init__.py` exports the public functions; private helpers prefixed `_`.
- `orchestrator/models/` — pydantic models, one file per family
  (`envelopes.py`, `api.py`, `settings.py`, `config.py`).
- `orchestrator/templates/comments/<name>.md.j2`, `pr_body.md.j2`.
- `console/app/<route>/page.tsx`, `layout.tsx`, `loading.tsx`, `error.tsx`;
  BFF handlers `console/app/api/console/<name>/route.ts`.
- `console/components/<Name>/<Name>.tsx` with `<Name>.module.css` beside it;
  server and client components in the same folder, client files carrying
  `'use client'` on line one.
- `console/lib/` — `api.ts`, `api.types.ts` (generated), `auth.ts`,
  `authz.ts` (generated), `github.ts`, `schemas.ts`, `types.ts`, `hooks/`.
- `cli/factory/<command>.py` — one module per subcommand.
- `prompts/<role>/system.md` and `user.md`.
- `migrations/NNNN_short_name.sql`.
- `fixtures/<kind>/` — `worked_examples/`, `eval_set/`, `envelopes/`,
  `webhooks/`, `known_failing/`.
- `tests/<level>/test_<module>.py` for Python; `console/**/*.test.ts(x)` for
  vitest; `tests/e2e/*.spec.ts` for Playwright.
- `context/specs/NN-unit-name.md` for unit specs; `00-build-plan.md` first.

## Naming

- Python: `snake_case` modules, functions and variables; `PascalCase`
  classes; `UPPER_SNAKE` constants; enum values equal the PRD string.
- TypeScript: `PascalCase` components and types; `camelCase` functions and
  variables; route folders lowercase; CSS Module classes `camelCase`.
- Database identifiers exactly as TRD §4.2; event kinds, gate names and
  statuses are the schema's CHECK values, nowhere re-spelled.
- Branch names: `feat/NN-unit-name` for units; `fix/<short>` for fixes.
- Commits: Conventional Commits with the area as scope,
  `feat(orchestrator): spec gate section check (unit 07)`.
