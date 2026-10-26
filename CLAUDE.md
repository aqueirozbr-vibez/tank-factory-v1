## Application Building Context

This repository is the factory repository of Tank Factory v1. Read the following
files in order before implementing or making any architectural decision:

1. `context/project-overview.md` — product definition, guiding principles,
   goals, features, and scope
2. `context/architecture.md` — stack, system boundaries, storage model, auth
   model, agent model, and invariants
3. `context/ui-context.md` — theme, colour tokens, typography, components,
   layout, and accessibility
4. `context/code-standards.md` — implementation rules for Python and
   TypeScript, API rules, testing, and file organisation
5. `context/ai-workflow-rules.md` — development workflow, scoping rules,
   protected files, and the verification checklist
6. `context/progress-tracker.md` — current phase, completed work, open
   questions, decisions, and next steps

Then read the unit spec you were given in `context/specs/`, and the PRD and
TRD sections it cites:

- `docs/tank-factory-v1-prd.md` (PRD v3.0) wins on behaviour.
- `docs/tank-factory-v1-trd.md` (TRD v1.0) wins on structure.
- `context/ui-context.md` carries the console's design rules (the design
  package they came from is not part of this repository).

Update `context/progress-tracker.md` after each meaningful implementation
change.

If implementation changes the architecture, scope, or standards documented in
the context files, update the relevant file before continuing.

Work on one unit at a time, inside the boundary it names, with no dependency
it does not list. Do not weaken a cap, threshold, timeout or protected path to
make something pass. Do not invent a label, status, gate, role, screen,
endpoint or metric the PRD or TRD does not name; raise it as an open question
instead.
