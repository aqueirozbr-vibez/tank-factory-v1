# Unit 54: Authorisation table

## Goal

The decision table `authz.yaml` (`(permission, action, condition) → allow`,
default deny) and the generator that produces `orchestrator/authz/authz.py`
and `console/lib/authz.ts` from it; the collaborator permission cache of
five minutes; fail-closed on an unknown permission; the application refuses
to start on an unparsable or stale table (PRD §Capability 6, §Non-functional
· Security; TRD §6.2; architecture.md · Authorisation).

## Design

Terminal only. `authz.yaml` at the repository root names the five GitHub
permission levels the table knows (`read`, `triage`, `write`, `maintain`,
`admin`), fourteen actions (read, plan_approve, governed_approve, cancel,
resume, risk_override, budget_raise, concurrency, admissions, kill_switch,
audit_decide, config_pr, eval_run, export), two conditions (`always`,
`codeowner`), the rules (read for every level; every write action for
write, maintain and admin; `governed_approve` only with `codeowner`) and
the expected cases. `orchestrator/authz/__init__.py` parses and validates
the table (`load_table`: any defect is a `ConfigError`), answers through
the generated module (`decide`: unknown permission, unknown action or no
matching row deny, with the reason named), checks at start that the
committed module carries the table's SHA-256 and rules (`verify_generated`,
called by `build_service`, so `factory serve` stops on a stale or broken
table) and resolves a login's permission (`resolve_permission`: bots and
anonymous actors are `none`; the GitHub answer is cached five minutes in
`user_permissions`; an unreachable GitHub with no fresh cache returns
`None`, which `decide` denies). `orchestrator/authz/generate.py` renders
`authz.py`, `console/lib/authz.ts` and `console/lib/authz.cases.json`
deterministically; `factory authz [--check]` writes or verifies them. The
pytest suite runs every case row against both `decide` and the generated
`allowed`; `console/lib/authz.test.ts` runs the same cases against the
TypeScript module once the console scaffold (unit 56) brings vitest. Unit
55 wires `decide` into every write endpoint; the read endpoints keep
trusting the console's verified actor through the service token.

## Implementation

- `authz.yaml`; `orchestrator/authz/{__init__,generate,authz}.py` (the last
  generated); `console/lib/{authz.ts,authz.cases.json,authz.test.ts}`.
- `cli/authz_cmd.py`, `factory authz [--check]`; `build_service` calls
  `verify_generated()`.
- `tests/unit/test_authz.py`.

## Dependencies

- Unit 17 (collaborator permission). `pyyaml` (present). No new package.

## Verify when done

- [ ] Every case row passes in pytest against `decide` and `allowed`; the
      committed `authz.py`, `authz.ts` and `authz.cases.json` match the
      table (`factory authz --check`)
- [ ] An unparsable table, an unknown action in a rule and an edited table
      each raise `ConfigError`; `factory serve` refuses to start on them
- [ ] The permission cache answers within five minutes without GitHub; an
      unreachable GitHub with no fresh cache denies
- [ ] The four quality commands pass; the vitest run is recorded as pending
      unit 56
