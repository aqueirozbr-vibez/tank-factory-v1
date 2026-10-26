# Unit 55: Control endpoints

## Goal

The write endpoints of TRD §6.1 under `/api/v1`: `POST /tasks/{id}/budget`,
`/tasks/{id}/resume`, `/tasks/{id}/risk-override`, `POST /audit/{id}`,
`/controls/concurrency`, `/controls/kill-switch`, `/controls/admissions`
(with `clear_incident`) and `POST /eval/runs`, each with `Idempotency-Key`,
the authz check, its bounds (a raise once at most 2×, N 1 to 10, the
kill-switch 1/min) and exactly one `control` event under the actor's login
(PRD §Capability 6; TRD §6.1, §6.2, §7.8; code-standards · API Routes).

## Design

Terminal only. `api/controls.py` runs one order for every write: parse the
pydantic request (`models/api.py` request models), identify the caller
(service token plus `X-Factory-Actor`, or loopback), resolve the actor's
GitHub permission (`authz.resolve_permission`, cached five minutes,
`None` when GitHub is unreachable), evaluate `authz.decide` (a denial is a
403 and a `control` event with `denied: true`), require `Idempotency-Key`
(a repeated key on the same route by the same actor returns the first
response; the same key with another body is 422), apply the rule's rate
limit, act through the service, write the `control` event, remember the
response (`idempotency_keys`, migration 0005). Resume applies the §7.8
rule through `Resume.apply`, shared with the label path; a risk override
is accepted until `pr_open`, records `tasks.risk_overridden_by`, an
`approvals` row with gate `risk_override` and the control, and the task
re-enters the approval wait on its next attempt; an audit decision goes
through `writer.decide_audit` for the current month (404 when not drawn,
409 when decided); concurrency is bounded by `CONCURRENCY_MIN` and
`CONCURRENCY_MAX`; the kill-switch keeps `KillSwitch.disable/enable`;
admissions pause or resume and may clear an incident by seq; an eval run
starts through the service's `start_eval` (404 unknown set, 422 under 20
items, 409 while one runs, 202 with the run id). The three write routes of
units 40, 41 and 51 move under `/api/v1` with the same gate; the export
stays a `GET` with the `export` action.

## Implementation

- `migrations/0005_idempotency.sql`; writer `recall_response`,
  `remember_response`.
- `orchestrator/api/controls.py` (`ControlDeps`, `gate`, `control_router`);
  request models in `models/api.py`; `create_app` mounts the router at
  `/api/v1`; the service builds `ControlDeps` with its scheduler,
  kill-switch, resume and eval starter; `intake/resume.py` gains `apply`.
- Tests: `tests/unit/test_controls.py`; the unit 40, 41 and 51 API tests
  follow the routes under the prefix.

## Dependencies

- Units 53, 54, 41. No new package.

## Verify when done

- [ ] Every control answers with its documented statuses; a denied actor
      gets 403 and one `control` event; a repeated key returns the first
      response and writes no second event; a different body under the key
      is 422; a missing key is 400
- [ ] The bounds hold: raise once at most 2×, N 1 to 10, kill-switch once a
      minute, eval sets of fewer than 20 items refused
- [ ] Every successful control writes exactly one `control` event with the
      login
- [ ] The four quality commands pass; `openapi.json` is regenerated
