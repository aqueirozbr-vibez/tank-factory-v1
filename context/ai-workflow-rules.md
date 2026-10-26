# AI Workflow Rules

Rules for the coding agent building this repository. Imperative. The factory
you are building enforces exactly this discipline on its own agents; hold
yourself to it.

## Approach

- Build spec-driven and incremental. The PRD and TRD in `docs/` define
  the product; the six files in `context/` define how it is built; the unit
  specs in `context/specs/` define each step.
- Implement only what the current unit spec says. Do not infer behaviour
  from memory of similar systems. When the spec is silent, read the PRD and
  TRD section it cites before deciding anything.
- Follow the PRD's five phases in order: build loop, spec and review,
  delivery and isolation, eval and KPIs, console. A unit from phase N never
  requires a component that phase N+1 owns (TRD §1 "must not need yet").
- Build backend contracts before UI wiring, security before the feature it
  protects, shells with placeholder data before real data, and install a
  dependency only in the unit that first uses it.

## Scoping Rules

- Work on one unit at a time. Finish it, verify it, record it, then stop.
- Stay inside the system boundary the unit names: orchestrator, console,
  cli, deploy, prompts, migrations. A unit that must touch two boundaries
  says so and names the shared contract (OpenAPI document, `authz.yaml`,
  SQL schema).
- Do not refactor, rename or "clean up" code outside the unit's files. Note
  the opportunity in `progress-tracker.md` instead.
- Do not add a dependency the unit spec does not list. If the work needs one,
  stop and add it to the spec's Dependencies section with the reason first.
- Do not write speculative abstractions, feature flags, or "for later" hooks.
  The out-of-scope list in `project-overview.md` is binding.
- Do not generate files the unit does not call for: no README per folder, no
  example scripts, no sample data outside `fixtures/`.

## When to Split Work

Split the unit, and say so before starting, when it combines:

- A schema migration and the code that reads the new column.
- An API endpoint and the console screen that consumes it.
- A harness adapter change and a phase that depends on it.
- A gate implementation and the prompt change that targets it.
- Any change to `prompts/`, `authz.yaml` or an instrument with any other
  change (each of those needs its own eval or policy run).
- Behaviour the context files do not define.

If a unit cannot be verified end to end with the checklist below in one
session, it is too large. Propose the split in `progress-tracker.md` and
build the first half.

## Handling Missing or Ambiguous Requirements

Resolve in this order and record the source in the code comment or the spec:

1. The PRD section for the capability (`docs/tank-factory-v1-prd.md`).
2. The TRD section that traces to it (`docs/tank-factory-v1-trd.md`).
3. `project-overview.md` Guiding Principles, then `architecture.md`
   Invariants.
4. If still open: stop, add an entry under Open Questions in
   `progress-tracker.md` with the two or three concrete options, pick the
   most conservative one (the one that denies, blocks, or does less), state
   that assumption in the unit spec, and continue.

Never invent product behaviour: no new label, status, gate, role, screen,
endpoint, event kind or metric that the PRD or TRD does not name. If one
seems necessary, it is an open question, not a decision.

Never weaken a cap, a threshold, a timeout or a protected path to make a test
pass or a flow simpler.

## Protected Files

Do not modify these unless the unit spec names them explicitly:

- `docs/tank-factory-v1-prd.md` and `docs/tank-factory-v1-trd.md` — the PRD
  and TRD. Read-only inputs.
- `context/project-overview.md`, `architecture.md`, `code-standards.md`,
  `ui-context.md`, `ai-workflow-rules.md` — change them only to record a
  decision the user made or a unit spec required; say what changed and why
  in `progress-tracker.md`.
- `migrations/*.sql` that already exist — add a new file, never edit.
- `orchestrator/authz/authz.py` and `console/lib/authz.ts` — generated from
  `authz.yaml`; regenerate, never hand-edit.
- `console/lib/api.types.ts` — generated from OpenAPI; regenerate.
- `console/styles/industry.css` — vendored design system; overrides go in
  `console-tokens.css`.
- `prompts/**` — a change requires its own unit and an eval run; never tweak
  a prompt inside a code unit.
- `uv.lock`, `package-lock.json` — change only through `uv add` or
  `npm install` in a unit that lists the dependency.
- `fixtures/worked_examples/` — the PRD's examples; a test that disagrees
  with them is wrong, not the fixture.

## Keeping Docs in Sync

Update the matching context file in the same change whenever implementation
changes:

- A folder, module or boundary → `architecture.md` System Boundaries.
- A table, column, retention rule or cap → `architecture.md` Storage Model
  or Invariants.
- A convention, tool or command → `code-standards.md`.
- A token, component, state or layout → `ui-context.md`.
- Scope, a goal or a success criterion → `project-overview.md` (only when
  the PRD itself changed; the PRD leads).
- Any decision, assumption, open question or completed unit →
  `progress-tracker.md`, every time.

## How the Agent Works Inside a Unit

- Start by reading `CLAUDE.md`, then the six context files, then the unit
  spec, then the PRD and TRD sections the spec cites.
- Write the tests the unit's verification checklist implies before or with
  the code. For instruments and gates, the fixture is the spec.
- Run the quality commands yourself before claiming the unit works; paste
  the actual output into the progress tracker's session note if anything was
  skipped or failed.
- Do not mark a unit complete with a failing test, a skipped check, a TODO
  in the path the spec covers, or an unrecorded assumption.
- Report what you did not do as plainly as what you did.

## Before Moving to the Next Unit

Every item true, with the command output seen, not assumed:

1. The unit works end to end within its defined scope, demonstrated the way
   its spec's verification checklist says (a test, a CLI run, a screen).
2. No invariant in `architecture.md` was violated, and no protected file was
   modified.
3. Orchestrator and CLI: `uv run ruff format --check .`, `uv run ruff check
   .`, `uv run mypy .`, `uv run pytest` all pass.
4. Console (when the unit touches it): `npm run lint`, `npm run typecheck`,
   `npm test`, `npm run build` all pass.
5. The OpenAPI document and the generated `api.types.ts`, `authz.py` and
   `authz.ts` are regenerated and committed if their sources changed.
6. No secret, token or real login appears in code, fixtures, tests or logs.
7. Every new name matches the PRD spelling; every new constant has the PRD's
   value.
8. `progress-tracker.md` lists the unit under Completed with the date, any
   decision under Architecture Decisions, and the next unit under Next Up.
9. The branch `feat/NN-unit-name` holds only this unit's changes.
