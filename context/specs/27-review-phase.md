# Unit 27: Reviewer prompt and review phase

## Goal

An independent reviewer that did not write the code and cannot write code
reads the spec, the diff, the builder's envelope and the gate evidence in a
fresh read-only session on the other vendor, and returns a `ReviewOutput`
whose consistency code checks (PRD §Capability 4). The verdict scoring and
the revision loop are unit 28.

## Design

No UI. Visible result in the trace: a `review` phase row per reviewer call
with its own tokens and cost, an `envelopes` row, `envelope_schema` and
`verdict_consistent` gate rows, and a `handoff` event carrying the
effective verdict and the acceptance criteria that received no verdict.

## Implementation

### prompts/reviewer/{system,user}.md

Prefix order (architecture.md · Agent model): system guide; the review
policy (`review-policy.yaml` text); the spec; the builder envelope; the
gate evidence; `git diff <base>...HEAD`; an optional correction. The guide
states the test-integrity rules, the Non-goals dismissal, the human-always
classes and the consistency rules code enforces. The reviewer never sees
the builder's transcript, the issue thread or the holdout checkout.

### orchestrator/prompt_builder.py

- `build_reviewer_prompt(guide, *, policy_text, spec_text,
  builder_envelope_text, gate_evidence, diff, correction=None)`; every
  input passes `forbid_secrets`, the diff included: a credential in the
  diff never leaves the installation and blocks the task for a human.

### orchestrator/gates/verdict.py

- `verdict_consistent_gate(envelope, *, ac_ids) -> VerdictCheck(result,
  missing_ac, hard)`. Hard violations: `approved = true` with a blocking
  finding (severity Blocker, `met = false`, or listed in `blocking[]`);
  `revision_needed` with no findings; `approved` not matching the verdict.
  Soft violation: an acceptance criterion without a `met` verdict backed by
  evidence or a location while the verdict is `accepted`. Ids are
  normalised (`ac 2` → `AC2`).

### orchestrator/phases/review.py

- `ReviewPhase(deps, *, task_id, worktree, spec_rel, run_id, attempt=1,
  seq_start=None)`; refuses a roster whose builder and reviewer share a
  vendor; the sequence continues from the run's last phase row.
- `run()`: budget check; inputs from the worktree and the trace (latest
  valid `BuildOutput`, the run's gate rows rendered as evidence, the
  base-to-HEAD diff); the reviewer call with tools `Read`, `Glob`, `Grep`
  and no resume on the first call; envelope gate (one re-prompt, then
  blocked `human_decision_needed`); `verdict_consistent` (one re-prompt;
  a hard violation after it blocks `human_decision_needed`; a soft one
  forces the verdict to `revision_needed` and logs `verdict_forced`);
  timeouts and infrastructure retries as in the plan phase.
- `ReviewOutcome(status, run_id, envelope, verdict, missing_ac,
  blocked_reason, evidence, reviewer_calls, reprompts, cost_usd,
  session_id, phase_id)`.

### Shared pieces

- `PhaseRecorder.finish(..., result=)` records tokens and cost from a
  harness result; `TraceWriter.last_phase_seq`, `gate_results_for_run`,
  `latest_envelope`; `constants.VERDICT_REPROMPT_CAP = 1`.

## Dependencies

- None new. The diff is passed whole (no cap in v1; revisit with real data).

## Verify when done

- [ ] Green review: `review` row pass with the reviewer's cost, gates
      `envelope_schema` and `verdict_consistent` pass, prompt holds the
      policy, spec, builder envelope, gate evidence and diff and not the
      builder's session; tools are `Read`, `Glob`, `Grep`; no resume
- [ ] `approved = true` with a Blocker finding is re-prompted once (resumed
      session, correction in the prompt) and then blocks
      `human_decision_needed`
- [ ] An accepted envelope that skips AC2 is re-prompted once, then the
      verdict is forced to `revision_needed` with `missing_ac = (AC2,)`
- [ ] `revision_needed` with no findings is re-prompted; with findings on
      one criterion it passes and reports the other as missing
- [ ] Same-vendor roster refused; credential-shaped diff blocks for a human
      without a reviewer call; invalid envelope twice blocks; timeout blocks
- [ ] The four quality commands pass
