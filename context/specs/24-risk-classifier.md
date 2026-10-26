# Unit 24: Risk classifier

## Goal

Compute a task's risk in code from the planner's `risk_inputs` and the
spec's Scope paths with the instrument in `.factory/risk.yaml`
(PRD §Task risk classification): the strongest tier, not the sum; size,
blast-radius and dependency multipliers; `risk:low` below 50, `risk:high`
from 50, `risk:governed` by tier A path; tier U at 0 goes back to the
planner for a spec correction. Write the class to the spec header, the
task row and an issue label, commit the spec, and bind the approval sha.

## Design

No UI. Visible result: the PRD's worked examples reproduce to the decimal;
a green plan ends with `risk`, `commit` rows, `risk: risk:low` in the spec
header, `tasks.risk`, `tasks.spec_sha` and the `risk:low` label.

## Implementation

### orchestrator/instruments/risk.py

- `RiskInstrument` (pydantic) parsed from the YAML: tiers A to E and U
  with base, path prefixes and keywords; `size` and `blast_radius` bands;
  `new_dependency` multipliers; thresholds; governed rule.
- `scope_paths(spec)`, `infer_tiers(instrument, paths)` (path-prefix
  matches only; the semantic tiers come from the planner's `tiers_touched`,
  because the PRD's fifth worked example makes a billing path the Scope did
  not name a tech-lead override, not an automatic class), `classify(instrument, scope_paths, inputs, worktree)`
  → `RiskBreakdown(tier, base, factors, score, risk_class, reasons,
  existing_paths)` with `equation()`, `payload()`, `correction_text()`;
  `override(breakdown, risk_class, by, reason)`.
- Unclassifiable when no tier can be determined or no Scope path exists
  (a path counts when it or its parent directory exists, so new files are
  allowed).

### orchestrator/phases/plan.py

- `PlanPhase(..., instrument: RiskInstrument | None)`. After the spec gate
  and the open-question check: classify; U → one more planner correction
  within the cap, else blocked `spec_gate`; otherwise a `risk` row, the
  header line `risk: <class>` written into the spec, `tasks.risk` and
  `risk_score`, the `risk:*` label, then a `commit` row that commits the
  spec and binds `tasks.spec_sha` to the blob. Without an instrument the
  spec is still committed.

### Writer

- `set_task_risk(task_id, risk, score, overridden_by)`.

## Dependencies

- None new.

## Verify when done

- [ ] Worked examples: C/60/1/none → 40 `risk:low`; B/300/3/none →
      109.2 `risk:high`; D/40/5/none → 30 `risk:low`; C/60/1/tenacity →
      60 `risk:high`
- [ ] `.factory/roster.yaml` in Scope → tier A → `risk:governed`; A plus D
      → A; `docs/` alone → E 10 → `risk:low`
- [ ] No Scope paths, or only paths that do not exist → U, score 0,
      unclassifiable; the plan phase sends one correction and then accepts
- [ ] Green plan: rows `plan`, `spec_gate`, `risk`, `commit`; spec header
      `risk: risk:low`; `tasks.risk = risk:low`, `risk_score = 40`;
      `spec_sha` is the committed blob; label `risk:low` added
- [ ] `override` records the class, login and reason
- [ ] The four quality commands pass
