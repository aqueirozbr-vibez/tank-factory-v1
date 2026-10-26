# Unit 15: Factory CLI, phase 1 commands

## Goal

Give the operator the phase 1 entry points: `factory dev seed` builds a
scratch product repository with ten hand-written specs and matching fake
scripts; `factory run-spec` runs the build slice on one spec with the fake
or the real harness after the startup self-test; `factory trace` prints a
task's events and cost; `factory verify` checks the chain.

## Design

Terminal only. Visible result: `dev seed` then `run-spec --issue 1` prints
`pass`, a commit on `agent/1` and the cost; `trace` lists the tool calls;
`verify` reports the chain head; a violation script ends in `blocked
(protected_path)` with exit code 1.

## Implementation

### cli/main.py

- Subcommands `dev seed`, `run-spec`, `trace`, `verify`; `--version` kept.
  Exit codes: 0 on pass or ok, 1 on blocked or a broken chain, 2 on usage.

### cli/seed.py

- `seed_repository(target, factory_root, with_violations)`: a git
  repository on `main` with `pyproject.toml` (ruff, mypy strict, pytest
  with `pythonpath = ["."]`), the `demo` package, `tests/unit/`,
  `tests/acceptance/test_holdout.py`, `CODEOWNERS`, a workflow file,
  `.factory/` copied from the shipped fixture with `quality.yaml` pointing
  the tools at the factory's own environment (`uv run --project
  <factory_root> ...`), `.factory-dev/known_failing/`, ten specs
  `specs/<n>-<name>.md` in the seven-section template, ten fake scripts
  `.factory-dev/scripts/<n>.json` whose effects satisfy the real quality
  commands, and with `--with-violations` ten more `<n>-violation.json`
  scripts that also edit `tests/acceptance/test_holdout.py`.

### cli/run_spec.py

- `run_spec(repo, issue, spec, harness, scripts, data_dir, org, budget,
  skip_self_test)`: opens or creates `trace.db` under the data directory,
  loads `.factory/`, runs the self-test against the seeded known-failing
  fixture unless skipped, ensures the installation row, admits the task
  idempotently, creates the worktree, runs `run_build_slice`, prints the
  outcome. The fake harness loads the task's script file by default.

### cli/trace_cmd.py, cli/verify_cmd.py

- `trace`: events for a task (optionally one phase) with seq, kind, actor,
  time and a payload summary, then tool-call count and total cost.
- `verify`: `verify_chain` over the range; prints the head hash or the
  first bad seq.

## Dependencies

- None new.

## Verify when done

- [ ] `factory dev seed <dir> --with-violations` creates one commit on
      `main`, ten specs, twenty scripts, `.factory/quality.yaml` with
      `--project`, and the known-failing copy
- [ ] `factory run-spec --repo <dir> --issue 1 --harness fake
      --skip-self-test` exits 0, prints `pass`, and `agent/1` has a commit
      authored by the factory
- [ ] `factory run-spec ... --issue 2 --scripts <2-violation.json>` exits 1
      and prints `blocked (protected_path)`
- [ ] `factory trace --task dev/<dir>#1` lists `tool_call` rows and a cost
      line; `factory verify` prints `chain verified`
- [ ] The four quality commands pass
