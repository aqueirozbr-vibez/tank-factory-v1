# Unit 23: Spec gate and open questions

## Goal

Decide in code whether the planner's spec is complete (PRD §Capability 2):
all seven sections, EARS criteria, every criterion mapped to a holdout test
that exists, an ADR cited or "No ADR applies."; send violations back to the
planner at most twice; block at once with `factory:needs-human` when a
holdout test is missing (a human omission) or when the spec or envelope
carries open questions, posting them in the issue's language.

## Design

No UI. Visible result: the fixture spec passes all four gates against a
holdout that has its tests; crafted specs fail each gate with the item
named; the plan phase corrects, blocks `missing_holdout` with a comment, or
blocks `open_questions` with the questions posted and the label applied.

## Implementation

### orchestrator/gates/spec.py

- `parse_spec(text) -> ParsedSpec(title, sections, order, criteria, plan,
  open_questions)`: `##` headings, numbered criteria with continuation
  lines, Test plan lines `AC<n> -> <test id>` (holdout ids live under
  `tests/acceptance/`), open questions minus "None".
- `spec_sections`, `spec_ears`, `spec_ac_holdout_map(spec, has_test)`
  (returns the result and the holdout ids that do not exist),
  `spec_adr_cited(spec, worktree)`; `run_spec_gates(text, has_test,
  worktree) -> SpecGateOutcome(results, first_failure, missing_holdout,
  open_questions)`.

### orchestrator/phases/plan.py

- `PlanPhase` takes `holdout: HoldoutPort` and `github: GitHubPort | None`.
  After the claims pass: refresh the holdout, run the spec gates in the
  same `spec_gate` row; `missing_holdout` → comment, label, blocked
  `missing_holdout`; a gate failure → correction within
  `SPEC_CORRECTION_CAP` (shared with claim corrections) else blocked
  `spec_gate`; open questions from the spec or the envelope → comment in
  the issue's language, label, blocked `open_questions`; otherwise
  `spec_written` with the parsed spec.

### Messages, fixtures, seed

- `messages.open_questions(questions, language)` in English or Portuguese;
  `messages.missing_holdout(ids)`.
- `fixtures/specs/418-invoice-due-date.md` and the seed specs map each
  criterion to a holdout id and name the unit test on a separate line.

## Dependencies

- None new.

## Verify when done

- [ ] The fixture spec parses into seven ordered sections, two criteria,
      a plan mapping AC1 and AC2 to holdout and unit ids, and no open
      questions; all four gates pass with a holdout that has the tests
- [ ] A spec missing Constraints fails `spec_sections`; "The system
      returns X" fails `spec_ears`; AC2 without a holdout id fails the map
      gate; a holdout id absent from the checkout is reported as missing; a
      Constraints section with neither an ADR nor the exact sentence fails
      `spec_adr_cited`
- [ ] Plan phase: unmapped criterion → one correction then success;
      missing holdout → blocked `missing_holdout` with the comment and
      label; open questions in a Portuguese issue → Portuguese comment, label,
      blocked `open_questions`; three failing specs → blocked `spec_gate`
- [ ] The seeded specs still pass the phase 1 gate's focused-test parsing
- [ ] The four quality commands pass
