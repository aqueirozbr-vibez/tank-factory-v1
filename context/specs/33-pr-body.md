# Unit 33: PR body from the trace

## Goal

The pull request description is rendered from trace rows, never written by
an agent (PRD §Capability 5; TRD §8 PR-Body): the spec, a gate table with
every gate's pass or fail and evidence, cost in USD and tokens per phase,
session ids, harness and model per role, and the advisory and dismissed
findings with the Non-goals line quoted. It replaces the provisional body
from unit 30.

## Design

Markdown on GitHub. Sections: header line, spec and risk line, review line,
Gates table, Phases table with totals, Roles table, Advisory findings,
Dismissed by Non-goals, the spec in a details block, the provenance line.

## Implementation

### orchestrator/templates/pr_body.py

- `pr_body_fields(writer, *, task_id, run_id, spec_rel, spec_text, branch,
  sha)`: task and installation rows; `phases_for_run` (name, owner,
  harness, harness version, model, attempt, status, tokens in, out, cache
  read and write, cost, session id); `gate_results_for_run` with evidence
  (first violations, command and exit code, or the check count); the run's
  last `verdict_policy` payload (verdict, conformance, strongest equation,
  advisory findings, dismissed findings with their Non-goals line); roles
  from the agent phases; revision count; totals.
- `render_pr_body(fields)`: the template with the field strictness of
  `render_template`.

### orchestrator/templates/pr_body.md.j2

- The full body; agent-authored text (finding problems, the spec) passes
  through `nomention`; table cells through `cell` (pipes and newlines
  escaped).

### orchestrator/phases/deliver.py

- Uses `pr_body_fields` and `render_pr_body` with the spec text from the
  worktree; the `verdict` argument is no longer needed for the body.

### Writer

- `latest_log_payload(run_id, key)`: the newest log event in the run
  carrying the key (here `verdict_policy`).

## Dependencies

- None new.

## Verify when done

- [ ] Snapshot `fixtures/pr_body/accepted.md` matches the body rendered from
      a hand-built trace with a failed then passed gate, three agent phases
      with tokens, cost and session ids, an advisory and a dismissed finding
- [ ] The field set equals the template's declared fields; an extra field is
      refused; `@login` in a finding or the spec is neutralised
- [ ] The delivery test still finds the gate row, the review phase row, the
      verdict line and the total cost in the opened PR's body
- [ ] The four quality commands pass
