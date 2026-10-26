# Unit 08: Permissions snapshot, diff and rollback

## Goal

Enforce the write boundary by evidence, not advice (architecture.md ·
Invariant 10): snapshot the workspace before an agent runs, diff it after,
classify every changed path against the protected set, the dependency
manifests, the role's write boundary and the builder's test directories,
roll back the violating paths, and record the gate and an incident.

## Design

No UI. Visible result: on a temp git repository a seeded edit under
`tests/acceptance/` is detected, rolled back and fails the gate 10 of 10
times; an edit under `src/` with a new test under `tests/unit/` passes.

## Implementation

### orchestrator/workspace/git.py

- `run_git(args, cwd, input_text, check)` with an explicit argv and the
  allow-listed environment; `GitError`.
- `head_sha`, `list_files` (tracked plus untracked not ignored, POSIX
  paths), `hash_files` via `git hash-object --stdin-paths`,
  `restore_tracked` via `git checkout --`.

### orchestrator/gates/permissions.py

- `Snapshot(head, files, tracked)`, `take_snapshot(worktree)`,
  `diff_since(worktree, snapshot) -> [PathChange(path, added|modified|deleted)]`.
- `classify(changes, writes, protected_paths, manifests, lockfiles,
  test_dirs)` in this order per path: lockfile or manifest →
  `dependency_change`; protected → `protected_path`; outside the role's
  `writes` → `outside_boundary`; a new file under `tests/` outside the
  builder's test directories → `test_location`.
- Patterns: `**` matches all; a trailing `/` is a directory prefix; any
  other entry matches the exact path or the basename (`CODEOWNERS`,
  lockfiles).
- `permissions_gate(worktree, snapshot, writes, protected_paths, ...,
  rollback=True) -> PermissionsOutcome(result, changes, violations,
  rolled_back, blocked_reason)`: rollback restores tracked violating paths
  from the index and deletes added ones; `blocked_reason` is
  `protected_path` for protected, boundary and test-location violations and
  `dependency_change` for manifests and lockfiles, protected winning.
- `record_permissions(writer, outcome, ctx)`: `write_gate` plus one
  `incident` event when a protected path was touched.

## Dependencies

- None new (git is on the machine).

## Verify when done

- [ ] A seeded edit to `tests/acceptance/test_holdout.py` is rolled back
      (content restored), the gate fails with `protected_path`, 10 of 10
- [ ] Editing `src/app.py` and adding `tests/unit/test_b.py` passes with one
      check per path
- [ ] Adding `tests/test_c.py` fails with `test_location` and the file is
      removed
- [ ] Editing `pyproject.toml` or `uv.lock` fails with `dependency_change`
- [ ] Deleting `CODEOWNERS` or adding `.github/workflows/x.yml` fails with
      `protected_path` and the tree is restored
- [ ] With the planner's boundary (`specs/`), `specs/418.md` passes and
      `src/x.py` fails with `outside_boundary`
- [ ] An unchanged workspace passes with the check "no changes"
- [ ] `record_permissions` writes the gate row and an `incident` event only
      for protected violations
- [ ] The four quality commands pass
