# Unit 11: Worktree workspace

## Goal

Give every task one branch and one isolated working directory: create
`agent/<issue>` from the default branch as a git worktree under the data
directory, commit by code with the factory identity, produce the diff
against the base, and remove the worktree while keeping the branch
(PRD §Capability 1, 3; architecture.md · Workspace).

## Design

No UI. Visible result: on a temp repository, create, commit, diff and
remove behave as specified and are idempotent on a second attempt.

## Implementation

### orchestrator/workspace/worktree.py

- `Worktree(repo, path, branch, base_sha)`.
- `default_branch(repo)`: `origin/HEAD` when set, else the current branch.
- `create_worktree(repo, issue, worktrees_dir, base=None)`: reuses an
  existing `agent/<issue>` branch (a new attempt on the same branch) or
  creates it from the base; returns the existing worktree when its path is
  already registered.
- `commit_all(path, message)`: `git add -A`; returns `None` when nothing is
  staged, else the new sha; the commit author is the factory identity from
  settings (`FACTORY_GIT_AUTHOR_NAME`, `FACTORY_GIT_AUTHOR_EMAIL`).
- `diff_against_base(path, base_sha)` (`git diff base...HEAD`),
  `changed_files_since(path, base_sha)`, `blob_sha(path, rel)` (the sha an
  approval is bound to), `remove_worktree(repo, path)` keeping the branch.

### orchestrator/models/settings.py

- `git_author_name` (default `factory`) and `git_author_email` (default
  `factory@localhost`).

## Dependencies

- None new.

## Verify when done

- [ ] `create_worktree` makes `agent/418` from the default branch, the
      worktree directory exists, and its HEAD equals the base sha
- [ ] Creating again returns the same worktree; after removal, creating
      again reuses the existing branch
- [ ] `commit_all` returns `None` with no changes and a new sha with
      changes, authored by the factory identity
- [ ] `diff_against_base` contains the change and `changed_files_since`
      lists the file; `blob_sha` of a committed file is 40 hex characters
- [ ] `remove_worktree` deletes the directory and keeps the branch
- [ ] `default_branch` reads `origin/HEAD` when present
- [ ] The four quality commands pass
