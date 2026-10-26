# Unit 09: Claim gates

## Goal

Check every claim an agent envelope makes against the workspace:
`artifacts_exist` (each claimed path is a file), `files_non_empty` (each has
content), `diff_matches_claims` (each claimed change appears in the
post-run diff). Evidence per path; the violation names the path.

## Design

No UI. Visible result: three `GateResult`s per envelope, each with one
check per claimed path; a missing, empty or unchanged claimed file fails the
right gate.

## Implementation

### orchestrator/gates/claims.py

- `artifacts_exist(worktree, paths)`, `files_non_empty(worktree, paths)`,
  `diff_matches_claims(changes, claimed)`; `claim_gates(worktree, changes,
  claimed) -> ClaimOutcome(results, first_failure)` running the three in
  the PRD's order.
- Claimed paths for the builder are `changed_files + tests_added`; for the
  planner, `artifacts`. `diff_matches_claims` also lists changed paths the
  agent did not claim as informational checks.
- A deleted file that is claimed passes `diff_matches_claims` (it changed)
  but fails `artifacts_exist`; the caller decides what the envelope meant.

## Dependencies

- None new.

## Verify when done

- [ ] All claimed paths present, non-empty and in the diff: three passing
      results with one check per path
- [ ] A claimed path that does not exist fails `artifacts_exist` naming it
- [ ] A claimed path with only whitespace fails `files_non_empty` naming it
- [ ] A claimed path that did not change fails `diff_matches_claims`
      naming it; an unclaimed change appears as an informational check
- [ ] `first_failure` is the first failing gate in order
- [ ] The four quality commands pass
