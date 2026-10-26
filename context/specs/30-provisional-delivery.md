# Unit 30: Provisional delivery

## Goal

After `accepted`, push `agent/<issue>` under the per-task App token, open
the pull request once with a short provisional body rendered from the
trace, apply `factory:review`, set `pr_open`; a merge event sets `merged`;
a second PR for the same task is never opened (PRD §Capability 5; TRD
§7.7). The full-suite run, the complete PR body and CI re-entry are phase
3.

## Design

No UI. Visible result on GitHub: the branch, one PR with the spec link,
the gate table, the review verdict and the cost per phase, labelled
`factory:review`; on a governed task the CODEOWNERs requested as
reviewers. In the trace: a `deliver` row, a `handoff` event with the PR
number and head sha, `tasks.pr_number`, `pr_head_sha`, `status = pr_open`.

## Implementation

### orchestrator/workspace/git.py

- `push_argv(remote, branch, token)` and `push_branch(cwd, branch, *,
  remote="origin", token=None)`: the token travels as a transient
  `http.extraheader` (`AUTHORIZATION: basic base64(x-access-token:TOKEN)`),
  never in the remote URL, never in the trace; the recorded command is the
  token-free form.

### orchestrator/templates/pr_body.md.j2

- Provisional body: issue and task, spec link on the branch, risk, head
  sha, verdict and conformance, the gate table, the phase table with
  harness, model and cost, the total. `render_template` generalises the
  comment renderer with the same field strictness.

### orchestrator/phases/deliver.py

- `DeliveryPort` (the GitHub slice: list pulls for a branch, create pull,
  add labels, request reviewers).
- `DeliverPhase(deps, *, task_id, worktree, spec_rel, run_id, github,
  base_branch, verdict=None, token=None, owners=(), attempt=1,
  seq_start=None)`: a `deliver` row; a merged task is a no-op; push with
  `INFRA_RETRIES` and backoff, then blocked `timeout`; reuse
  `tasks.pr_number`, else adopt the branch's existing PR, else create one
  titled from the builder's commit message; `factory:review` and CODEOWNER
  reviewers on creation; `set_task_pr`, `pr_open`, `handoff`.
- `MergeObserver.on_merged(event)`: the task by PR number or branch →
  `merged`, a log event; an unmerged close is logged and ignored.

### orchestrator/issue_to_pr.py

- `deliver(...)` constructs and runs the phase; the full composition
  (plan → approval → build → review → deliver) is unit 31.

### Writer readers

- `phases_for_run`, `task_for_pr`, `task_for_branch`.

## Dependencies

- None new. The real push and PR need the user's GitHub App and scratch
  repository (phase 2 gate); here the remote is a local bare repository
  and GitHub is a fake.

## Verify when done

- [ ] After an accepted review the branch lands in the remote at HEAD, one
      PR is created with the commit-message title, body with spec link,
      gate rows, verdict and cost; `factory:review` added; task `pr_open`
      with number and head sha; `deliver` row pass; `handoff` event
- [ ] A second deliver call pushes again and creates nothing; a task
      without a recorded PR adopts the branch's existing PR
- [ ] Governed: individual owners requested as reviewers, teams as team
      reviewers
- [ ] A missing remote blocks `timeout` after the retries with the
      recorded sleeps and a token-free command in the evidence
- [ ] `push_argv` carries the token only in the extra header
- [ ] Merge observer: the PR's task becomes `merged`; unknown PR and
      unmerged close are ignored; the router routes `pull_request_merged`
- [ ] The four quality commands pass
