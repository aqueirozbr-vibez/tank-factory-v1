"""Unit 07: envelope models and the envelope_schema gate."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from orchestrator.gates import GateResult, envelope_schema_gate, record_envelope, write_gate
from orchestrator.gates.base import Check, GateContext, Violation
from orchestrator.models.enums import (
    AgentRole,
    GateName,
    OutputType,
    PhaseKind,
    PhaseName,
    WorkspaceKind,
)
from orchestrator.models.envelopes import (
    BuildOutput,
    Finding,
    PlanOutput,
    ReviewOutput,
    RiskInputs,
    VerifyOutput,
    envelope_model_for,
)
from orchestrator.trace import TraceWriter, verify_chain

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def _green_envelope() -> dict[str, object]:
    script = json.loads((FIXTURES / "envelopes" / "builder_green.json").read_text(encoding="utf-8"))
    envelope: dict[str, object] = script["envelope"]
    return envelope


def _stream_structured_output() -> dict[str, object]:
    lines = (FIXTURES / "streams" / "claude_build_green.jsonl").read_text(encoding="utf-8")
    result = json.loads(lines.strip().splitlines()[-1])
    structured: dict[str, object] = result["structured_output"]
    return structured


def _violations(exc: ValidationError) -> list[str]:
    return [".".join(str(p) for p in e["loc"]) for e in exc.errors()]


# --- models -----------------------------------------------------------------


def test_schema_is_closed_and_lists_required_fields() -> None:
    schema = BuildOutput.json_schema()
    assert schema["additionalProperties"] is False
    required = schema["required"]
    assert isinstance(required, list)
    assert set(required) == {"status", "summary", "commit_message"}
    assert "$schema" not in schema
    assert envelope_model_for(AgentRole.BUILDER) is BuildOutput
    assert envelope_model_for(AgentRole.PLANNER) is PlanOutput
    assert envelope_model_for(AgentRole.REVIEWER) is ReviewOutput


def test_green_envelopes_validate() -> None:
    fixture = BuildOutput.model_validate(_green_envelope())
    stream = BuildOutput.model_validate(_stream_structured_output())
    assert fixture == stream
    assert fixture.commit_message == "feat(billing): invoice due_date (#418)"


def test_build_output_violations() -> None:
    base = _green_envelope()
    missing = dict(base)
    del missing["commit_message"]
    with pytest.raises(ValidationError) as exc:
        BuildOutput.model_validate(missing)
    assert _violations(exc.value) == ["commit_message"]

    extra = dict(base, surprise=1)
    with pytest.raises(ValidationError) as exc:
        BuildOutput.model_validate(extra)
    assert _violations(exc.value) == ["surprise"]

    escaping = dict(base, changed_files=["../etc/passwd"])
    with pytest.raises(ValidationError, match="relative path") as exc:
        BuildOutput.model_validate(escaping)
    assert _violations(exc.value) == ["changed_files"]

    absolute = dict(base, tests_added=["/tmp/x.py"])
    with pytest.raises(ValidationError, match="tests_added"):
        BuildOutput.model_validate(absolute)


def test_plan_output_requires_a_spec_artifact() -> None:
    risk = RiskInputs(tiers_touched=["C"], est_lines=60, modules=["billing"])
    plan = PlanOutput(
        status="success",
        summary="Spec written",
        artifacts=["specs/418-invoice-due-date.md"],
        risk_inputs=risk,
    )
    assert plan.open_questions == []
    with pytest.raises(ValidationError, match="spec path"):
        PlanOutput(status="success", summary="x", artifacts=["notes.md"], risk_inputs=risk)
    with pytest.raises(ValidationError):
        RiskInputs(tiers_touched=["Z"], est_lines=1)
    with pytest.raises(ValidationError):
        RiskInputs(est_lines=-1)


def test_review_output_and_findings() -> None:
    finding = Finding(
        requirement_id="AC 3",
        met=False,
        severity="Major",
        file="src/api/invoices.py",
        line=41,
        problem="due_date omitted from the list response",
        evidence="src/api/invoices.py:41",
    )
    review = ReviewOutput(
        approved=False, verdict="revision_needed", findings=[finding], blocking=["AC 3"]
    )
    assert review.findings[0].severity == "Major"
    with pytest.raises(ValidationError) as exc:
        Finding(requirement_id="AC 1", met=True, severity="Critical", problem="x")
    assert _violations(exc.value) == ["severity"]
    with pytest.raises(ValidationError) as exc:
        Finding(requirement_id="AC 1", met=True, severity="Nit", line=0, problem="x")
    assert _violations(exc.value) == ["line"]
    with pytest.raises(ValidationError):
        ReviewOutput(approved=True, verdict="maybe")


def test_verify_output_caps_the_tail() -> None:
    verify = VerifyOutput(
        status="fail",
        gate="typecheck",
        checks=[{"item": "uv run mypy src/", "ok": False, "note": "1 error"}],
        violations=["src/billing/invoice.py:88: incompatible type"],
        command="uv run mypy src/",
        exit_code=1,
        output_tail="x" * 5000 + "END",
    )
    assert len(verify.output_tail) == 4000 and verify.output_tail.endswith("END")
    assert verify.checks[0].ok is False


# --- gate -------------------------------------------------------------------


def test_gate_outcomes() -> None:
    result, model = envelope_schema_gate(None, BuildOutput)
    assert not result.passed and model is None
    assert "no envelope" in result.violations[0].problem

    result, model = envelope_schema_gate("not json", BuildOutput)
    assert not result.passed and "not JSON" in result.violations[0].problem

    result, model = envelope_schema_gate("[1]", BuildOutput)
    assert not result.passed and "JSON object" in result.violations[0].problem

    result, model = envelope_schema_gate(json.dumps({"status": "success"}), BuildOutput)
    assert not result.passed and model is None
    assert {v.item for v in result.violations} == {"summary", "commit_message"}
    assert "Gate envelope_schema failed." in result.correction_text()
    assert "- commit_message:" in result.correction_text()

    result, model = envelope_schema_gate(json.dumps(_green_envelope()), BuildOutput)
    assert result.passed and isinstance(model, BuildOutput)
    assert result.gate is GateName.ENVELOPE_SCHEMA
    assert [c.ok for c in result.checks] == [True, True, True]


def test_write_gate_and_record_envelope(trace_writer: TraceWriter) -> None:
    task_id = "acme/atlas-api#418"
    trace_writer.insert_task(
        task_id=task_id,
        issue_number=418,
        title=None,
        branch="agent/418",
        workspace_kind=WorkspaceKind.WORKTREE,
        budget_usd=6.0,
        admitted_by="rsouza",
        config_sha="cfg1",
    )
    run_id = trace_writer.start_run(
        task_id=task_id, attempt=1, factory_version="0.1.0", config_sha="cfg1"
    )
    phase_id = trace_writer.start_phase(
        run_id=run_id,
        seq=1,
        name=PhaseName.BUILD,
        kind=PhaseKind.AGENT,
        owner="builder",
        timeout_s=10,
    )
    ctx = GateContext(task_id=task_id, run_id=run_id, phase_id=phase_id, attempt=2)
    result, _ = envelope_schema_gate(json.dumps({"status": "success"}), BuildOutput)
    gate_id = write_gate(trace_writer, result, ctx)
    envelope_id = record_envelope(
        trace_writer,
        run_id=run_id,
        phase_id=phase_id,
        agent=AgentRole.BUILDER,
        output_type=OutputType.BUILD,
        text='{"status": "success"}',
        result=result,
        attempt=2,
    )
    row = trace_writer.db.fetchone("SELECT * FROM gate_results WHERE id = ?", (gate_id,))
    assert row is not None
    assert (row["gate"], row["passed"], row["attempt"]) == ("envelope_schema", 0, 2)
    assert "commit_message" in row["violations_json"]
    event = trace_writer.db.fetchone(
        "SELECT kind, payload_json FROM events WHERE phase_id = ? ORDER BY seq DESC LIMIT 1",
        (phase_id,),
    )
    assert event is not None and event["kind"] == "gate_fail"
    payload = json.loads(event["payload_json"])
    assert payload["gate_results_id"] == gate_id and payload["violations"] == 2
    env = trace_writer.db.fetchone("SELECT * FROM envelopes WHERE envelope_id = ?", (envelope_id,))
    assert env is not None
    assert env["valid"] == 0 and "summary" in env["violations_json"]

    good = GateResult(GateName.LINT, True, (Check("ruff", True),))
    write_gate(trace_writer, good, ctx)
    kinds = [
        r["kind"]
        for r in trace_writer.db.fetchall(
            "SELECT kind FROM events WHERE phase_id = ? ORDER BY seq", (phase_id,)
        )
    ]
    assert kinds == ["gate_fail", "gate_pass"]
    assert verify_chain(trace_writer.db).ok


def test_correction_text_includes_command_and_tail() -> None:
    result = GateResult(
        GateName.TYPECHECK,
        False,
        violations=(Violation("src/x.py:88", "incompatible type"),),
        command="uv run mypy src/",
        exit_code=1,
        output_tail="y" * 5000,
    )
    text = result.correction_text()
    assert "Command: uv run mypy src/ (exit code 1)" in text
    tail = text.rsplit("Last output:", 1)[1].strip()
    assert tail == "y" * 4000
