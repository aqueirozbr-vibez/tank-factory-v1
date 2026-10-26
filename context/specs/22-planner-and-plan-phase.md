# Unit 22: Planner guide and plan phase

## Goal

Run the planner as one bounded phase (PRD §Capability 2): it receives the
tagged issue, the spec template with the gate rules, the repository's
`CLAUDE.md` or `AGENTS.md` and the ADR index, writes exactly one file under
`specs/`, and returns a `PlanOutput`. Code checks the envelope, the write
boundary and the claims; the spec's content is judged by unit 23.

## Design

No UI. Visible result: with a fake planner script the spec appears at the
path the planner was given, the claims verify, `tasks.spec_path` is set,
and the assembled prompt carries the five sections in the TRD §8 order; a
write outside `specs/` is rolled back and blocks the task.

## Implementation

### prompts/planner/system.md, user.md, spec-template.md

- The guide: what the planner receives, the seven rules code enforces
  (one file under `specs/`, every section, EARS criteria, Test plan maps
  every criterion to a holdout id from the issue, Constraints cite an ADR
  or say "No ADR applies.", no technology the issue did not name, open
  questions when the issue is incomplete), honest `risk_inputs`, the
  `PlanOutput` contract.
- The user template order: spec path and template with gate rules;
  repository guidance; ADR index; the issue as data; an optional correction
  tail on a re-prompt.
- The spec template: header lines and the seven sections with the exact
  wording the gate checks.

### orchestrator/prompt_builder.py

- `load_prompt_text(role, name, override_dir)` for extra guide files.
- `build_planner_prompt(guide, spec_path, spec_template, issue_block,
  agents_md, adr_index, correction)` over the planner allow-list, with
  `forbid_secrets` on every text.

### orchestrator/phases/plan.py

- `spec_path_for(issue, title)` → `specs/<issue>-<slug>.md`.
- `repository_guidance(worktree)` reads `CLAUDE.md` or `AGENTS.md` (first
  found, capped) and `adr_index(worktree)` lists `docs/adr/*.md` with
  their first heading.
- `PlanPhase.run()`: budget check; snapshot; one `plan` phase row per
  planner call and one `spec_gate` row per round; `envelope_schema` with
  one re-prompt, then blocked `spec_gate`; `permissions_enforce` with
  `writes = ("specs/",)` → rollback and blocked `protected_path`; claim
  gates → a correction re-prompt within `SPEC_CORRECTION_CAP`, then
  blocked `spec_gate`; harness timeout, budget, max turns and infra errors
  as in the build phase; a `PromptError` blocks `spec_gate` with the reason;
  success sets `tasks.spec_path` and returns the envelope and spec text.

## Dependencies

- None new.

## Verify when done

- [ ] Green: `plan` and `spec_gate` rows pass, the spec file exists at the
      given path, `tasks.spec_path` is set, cost is recorded, and the prompt
      contains the spec path, the template heading, the `AGENTS.md` text, the
      ADR entry and the `<issue number="418"` block in that order
- [ ] A script writing `src/x.py` blocks `protected_path` after rollback
- [ ] Invalid envelope then valid: two calls; an unclaimed or missing
      artifact: one correction then success; three claim failures: blocked
      `spec_gate` after three calls
- [ ] A credential shape in `AGENTS.md` blocks `spec_gate` before any call
- [ ] The four quality commands pass
