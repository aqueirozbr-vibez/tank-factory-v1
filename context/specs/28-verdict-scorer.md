# Unit 28: Verdict scorer and revision loop

## Goal

Compute the review verdict in code from the reviewer's findings with
`.factory/review-policy.yaml` (PRD §Review verdict policy): the strongest
adjusted finding, not the sum; conformance 100 minus it; `accepted` at 70;
`human_decision_needed` as a class. Run the revision loop: findings back to
the builder, at most two rounds; a third `revision_needed` or any human
class labels `factory:needs-human` and posts the findings (PRD §Capability
4).

## Design

No UI. Visible result: the PRD's five worked examples reproduce to the
decimal; a `verdict_policy` log event per review carries the equation; a
`revision` build row follows a `revision_needed`; the needs-human comment
lists the findings, the dismissed ones with the Non-goals line quoted, and
the prior verdicts.

## Implementation

### orchestrator/instruments/verdict.py

- `ReviewPolicy.from_yaml` / `load(config_dir)`.
- `diff_lines(diff)`: new-side line numbers per file from the unified
  diff hunks (context and added lines).
- `score_finding(policy, finding, spec, lines)`: tier from the severity,
  demoted to `Unverifiable` (0) when the cited `file:line` is not in the
  diff; spec link 1.0 for `AC<n>`, `Scope <n>` or a Scope path, 0.9 for a
  Constraint or ADR reference, 0.5 otherwise; evidence 1.0 with a location
  plus a test id, reproduction or failure, 0.8 with a location only, 0.4
  without a location; scope 0 when the finding is dismissed by Non-goals
  (an explicit citation, or a stem that appears in a Non-goals line and
  nowhere else in the spec), with the line quoted. `met = true` findings
  are verdicts on criteria, not scored.
- `score_review(policy, envelope, *, spec, diff, missing_ac,
  revisions_so_far)`: blocking score, conformance, human reasons (the
  reviewer's own `human_decision_needed`, a security Blocker, a third
  revision), the verdict; `missing_ac` forces `revision_needed`.
- `override_verdict` and `record_verdict_override`: the tech lead's call,
  written as an `audit` event under the login with the reviewer's model and
  the policy version (the monthly `audit_decisions` row is phase 5).

### orchestrator/issue_to_pr.py

- `run_build_and_review(deps, *, task_id, worktree, spec_rel, github,
  issue_number, attempt)`: build; then review, score, and either accept,
  send the actionable findings (not dismissed; a synthesized note for each
  criterion without a verdict) to a `revision` build, or stop for a human.
  `REVISION_CAP` from constants; the YAML's `revision_cap` is informative.
  `PipelineOutcome(status, run_id, build, reviews, scores, revisions,
  blocked_reason, evidence)`.

### orchestrator/phases/build.py

- `BuildPhase(..., reviewer_findings=(), seq_start=None)`: a revision round
  uses the `revision` phase name, adds the findings section to the prompt,
  continues the run's sequence, and treats an unchanged tree as a pass on
  the current HEAD (the builder may argue a finding does not apply).

### orchestrator/intake/messages.py

- `review_findings(payload, prior)`: the needs-human text (unit 29 moves
  it into a template).

## Dependencies

- None new.

## Verify when done

- [ ] Worked examples: Major on AC3 with location only → 56, conformance
      44, `revision_needed`; Minor naming no AC without a location → 6, 94,
      `accepted`; Blocker on AC1 with a failing test → 100, 0,
      `revision_needed`, and `human_decision_needed` with `third_revision`
      after two revisions; Major about caching → 0, dismissed with
      "Caching of invoice queries." quoted; override recorded with login,
      reason, reviewer model and policy version
- [ ] A cited line outside the diff scores 0; a security Blocker is a human
      class; policy YAML validation
- [ ] Loop: `revision_needed` then accepted after one revision (a
      `revision` row, findings in the builder prompt); third
      `revision_needed` stops with `revision_cap`, comment and label; the
      reviewer's `human_decision_needed` stops at once; a forced revision
      hands the builder a note per missing criterion
- [ ] The four quality commands pass
