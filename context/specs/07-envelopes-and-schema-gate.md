# Unit 07: Envelope models and the schema gate

## Goal

Define the four envelopes the PRD names (`PlanOutput`, `BuildOutput`,
`ReviewOutput`, `VerifyOutput`) as pydantic models that export the JSON
schema passed to the harness, a shared `GateResult` type with one helper
that writes gate rows and events, and the `envelope_schema` gate that turns
harness text into a validated model or a violation list recorded on the
`envelopes` row.

## Design

No UI. Visible result: the green fixture's envelope validates; a missing
field, an extra field, an unsafe path or a bad severity each produce a
named violation; the gate writes `gate_results`, `envelopes` and a
`gate_pass` or `gate_fail` event.

## Implementation

### orchestrator/models/envelopes.py

- `Envelope` base with `extra="forbid"` (the exported schema carries
  `additionalProperties: false`) and `json_schema()`.
- `PlanOutput {status, summary, artifacts[], notes_for_next_agent,
  risk_inputs{tiers_touched[], est_lines, new_dependencies[], modules[]},
  open_questions[]}`; `artifacts` must include a `specs/*.md` path.
- `BuildOutput {status, summary, changed_files[], tests_added[],
  commit_message, notes_for_next_agent}`; every path is relative and
  contains no `..`.
- `Finding {requirement_id, met, severity, file, line, problem, impact,
  correction, evidence}` with the PRD severities; `ReviewOutput {approved,
  verdict, findings[], blocking[]}` with the PRD verdicts.
- `VerifyOutput {status, gate, checks[], violations[], command, exit_code,
  output_tail}` produced by code for the repair prompt; `output_tail` is
  cut to its last 4,000 characters.
- `ENVELOPE_MODELS` by `OutputType` and `envelope_model_for(role)`.

### orchestrator/gates/base.py

- `Check(item, ok, note)`, `Violation(item, problem)`, `GateResult(gate,
  passed, checks, violations, command, exit_code, output_tail)` with
  `correction_text()` for the agent and `write_gate(writer, result, ...)`
  writing the `gate_results` row plus one `gate_pass` or `gate_fail` event
  that names the row id.

### orchestrator/gates/envelope_schema.py

- `envelope_schema_gate(text, model) -> (GateResult, model | None)`:
  no text, invalid JSON, a non-object, or a pydantic error each fail with
  the location and message as violations.
- `record_envelope(writer, ..., text, outcome)` writing the `envelopes`
  row with `valid` and `violations_json`.

## Dependencies

- None new.

## Verify when done

- [ ] `BuildOutput.json_schema()` lists the required fields and sets
      `additionalProperties` to false; `envelope_model_for(builder)` is
      `BuildOutput`
- [ ] The green fixture envelope and the green stream's `structured_output`
      validate as `BuildOutput`
- [ ] Missing `commit_message`, an unknown field, `../x` in
      `changed_files`, a `PlanOutput` without a spec artifact, a `Finding`
      with `line` 0 and an unknown severity each fail with a violation naming
      the field
- [ ] `envelope_schema_gate(None, ...)` fails with "no envelope";
      `"not json"` fails with "not JSON"; a valid text passes and returns the
      model
- [ ] `write_gate` stores the row and a `gate_fail` event whose payload
      carries the gate name and row id; `record_envelope` stores `valid = 0`
      with the violations
- [ ] `VerifyOutput` keeps only the last 4,000 characters of `output_tail`
- [ ] The four quality commands pass
