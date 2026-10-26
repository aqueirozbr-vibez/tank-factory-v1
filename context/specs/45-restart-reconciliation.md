# Unit 45: Restart reconciliation

## Goal

A restart reads GitHub before acting, adopts an existing PR by branch name,
resumes `ci_wait` without a new PR, and restarts agent phases with attempt
+ 1 and unchanged caps, so five restarts mid-task open zero duplicate PRs
and zero duplicate comments (PRD §Capability 5 · idempotent restart; TRD
§7.8, §10 "duplicate PRs or comments after a crash").

## Design

Terminal only. `TaskRunner.run` now creates the worktree, reconciles, then
chooses the resume point. Reconciliation reads the PRs for the branch, the
issue labels and the remote branch head, writes nothing to GitHub, adopts
an open PR into `tasks.pr_number` when the row has none (the crash between
`create_pull` and the write), and logs the findings on the new run. The
resume point gains `review`: when the branch head is a build commit the
trace handed off and no review was accepted, the run skips the build and
starts at the review with a prior build outcome, so a crash between the
commit and the review does not re-run the builder on a finished tree. The
plan-approval phase announces the wait once per spec sha: a restart during
the wait re-adds the label but does not repost the comment. In container
mode the harness versions are read through the task container
(`<binary> --version` via the exec runner) when the container starts and
handed to the adapters, so the orchestrator image needs no CLI binaries;
the host probe at service start applies to worktree mode only.

## Implementation

- `orchestrator/runner.py`: `_reconcile`, `ResumePoint` with `review`,
  `resume_point(head_sha=)`, `_start_container` returning the versions,
  `_real_binaries`, `build_deps(role, runner, versions)`, `HarnessFactory`
  with versions, `RunnerGitHub.list_labels` and `get_branch_sha`.
- `orchestrator/issue_to_pr.py`: `build_and_review(prior_build=)`.
- `orchestrator/trace/writer.py`: `last_build_commit`, `awaiting_logged`.
- `orchestrator/phases/approval.py`: the announcement is idempotent.
- `orchestrator/harness/registry.py`: `read_harness_versions`,
  `make_harness(version=)`; the service probes on the host in worktree mode
  only.

## Dependencies

- Units 31 and 34. No new package.

## Verify when done

- [ ] Five crashes (at build start, review start, deliver start, after
      `create_pull`, at `ci_wait` start) and a final start open exactly one
      PR, post no comment, call each agent once, and enter at plan, build,
      review, deliver, deliver, deliver with attempts 1 to 6
- [ ] A plan-review wait restarted mid-wait posts no second comment and
      still completes on the approval label
- [ ] Versions read through a runner parse both CLIs and fail closed on a
      missing binary; an adapter built with a known version reports it
- [ ] The four quality commands pass
