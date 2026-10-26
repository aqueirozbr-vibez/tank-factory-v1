"""``envelope_schema``: harness text becomes a validated model or a violation list."""

import json

from pydantic import ValidationError

from orchestrator.gates.base import Check, GateResult, Violation, failed, passed
from orchestrator.models.enums import AgentRole, GateName, OutputType
from orchestrator.models.envelopes import Envelope
from orchestrator.trace.writer import TraceWriter


def envelope_schema_gate[E: Envelope](
    text: str | None, model: type[E]
) -> tuple[GateResult, E | None]:
    name = model.__name__
    if text is None or not text.strip():
        return failed(
            GateName.ENVELOPE_SCHEMA,
            [Violation("envelope", f"no envelope returned; expected {name} JSON")],
        ), None
    try:
        raw = json.loads(text)
    except ValueError as exc:
        return failed(
            GateName.ENVELOPE_SCHEMA,
            [Violation("envelope", f"not JSON: {exc}")],
            [Check("json parsed", False, str(exc))],
        ), None
    if not isinstance(raw, dict):
        return failed(
            GateName.ENVELOPE_SCHEMA,
            [Violation("envelope", f"expected a JSON object, got {type(raw).__name__}")],
            [Check("json parsed", True)],
        ), None
    try:
        envelope = model.model_validate(raw)
    except ValidationError as exc:
        violations = [
            Violation(".".join(str(p) for p in err["loc"]) or name, err["msg"])
            for err in exc.errors()
        ]
        return failed(
            GateName.ENVELOPE_SCHEMA,
            violations,
            [Check("json parsed", True), Check(f"schema valid: {name}", False)],
        ), None
    checks = [
        Check("json parsed", True),
        Check(f"schema valid: {name}", True),
        Check("fields", True, f"{len(raw)} fields"),
    ]
    return passed(GateName.ENVELOPE_SCHEMA, checks), envelope


def record_envelope(
    writer: TraceWriter,
    *,
    run_id: str,
    phase_id: str,
    agent: AgentRole,
    output_type: OutputType,
    text: str | None,
    result: GateResult,
    attempt: int,
) -> str:
    """Write the ``envelopes`` row for a harness return, valid or not."""
    return writer.write_envelope(
        run_id=run_id,
        phase_id=phase_id,
        agent=agent,
        output_type=output_type,
        payload_json=text,
        valid=result.passed,
        attempt=attempt,
        violations=[{"item": v.item, "problem": v.problem} for v in result.violations] or None,
    )
