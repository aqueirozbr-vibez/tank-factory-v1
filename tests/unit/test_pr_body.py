"""Unit 33: the PR body renders from trace rows; snapshot under fixtures/pr_body."""

from pathlib import Path

import pytest

from orchestrator.models.enums import (
    EventKind,
    GateName,
    PhaseKind,
    PhaseName,
    PhaseStatus,
    TaskStatus,
    WorkspaceKind,
)
from orchestrator.templates import template_fields
from orchestrator.templates.pr_body import gate_evidence, pr_body_fields, render_pr_body
from orchestrator.trace import TraceWriter
from tests.unit.test_build_phase import make_config
from tests.unit.test_spec_gate import SPEC_REL, SPEC_TEXT, TASK

SNAPSHOT = Path(__file__).resolve().parents[2] / "fixtures" / "pr_body" / "accepted.md"
SHA = "b" * 40
SPEC_WITH_MENTION = SPEC_TEXT.replace("## Outcome\n\n", "## Outcome\n\nAsk @rsouza when unsure. ")


def seed_trace(writer: TraceWriter) -> str:
    cfg = make_config()
    writer.load_config_version(**cfg.config_version_row())
    writer.insert_task(
        task_id=TASK,
        issue_number=418,
        title="Invoice due date",
        branch="agent/418",
        workspace_kind=WorkspaceKind.WORKTREE,
        budget_usd=6.0,
        admitted_by="rsouza",
        config_sha=cfg.config_sha,
        status=TaskStatus.RUNNING,
    )
    writer.set_task_spec(TASK, spec_path=SPEC_REL, spec_sha="a" * 40)
    writer.set_task_risk(TASK, risk="risk:low", score=40.0)
    run_id = writer.start_run(
        task_id=TASK, attempt=1, factory_version="0.1.0-test", config_sha=cfg.config_sha
    )

    def phase(
        seq: int,
        name: PhaseName,
        kind: PhaseKind,
        owner: str,
        *,
        harness: str | None = None,
        version: str | None = None,
        model: str | None = None,
        session: str | None = None,
        tokens: tuple[int, int, int, int] = (0, 0, 0, 0),
        cost: float = 0.0,
        attempt: int = 1,
    ) -> str:
        phase_id = writer.start_phase(
            run_id=run_id,
            seq=seq,
            name=name,
            kind=kind,
            owner=owner,
            timeout_s=60,
            attempt=attempt,
            harness=harness,
            harness_version=version,
            model_id=model,
            session_id=session,
        )
        writer.finish_phase(
            phase_id,
            PhaseStatus.PASS,
            tokens_in=tokens[0],
            tokens_out=tokens[1],
            tokens_cache_read=tokens[2],
            tokens_cache_write=tokens[3],
            cost_usd=cost,
            session_id=session,
        )
        return phase_id

    phase(
        1,
        PhaseName.PLAN,
        PhaseKind.AGENT,
        "planner",
        harness="claude_code",
        version="2.1.309",
        model="claude-opus-5-5",
        session="plan-1",
        tokens=(8000, 900, 5000, 0),
        cost=0.21,
    )
    spec_gate = phase(2, PhaseName.SPEC_GATE, PhaseKind.CODE, "orchestrator")
    phase(3, PhaseName.COMMIT, PhaseKind.CODE, "orchestrator")
    phase(
        4,
        PhaseName.BUILD,
        PhaseKind.AGENT,
        "builder",
        harness="claude_code",
        version="2.1.309",
        model="claude-opus-5-5",
        session="sess-green",
        tokens=(18450, 2210, 12000, 640),
        cost=0.41,
    )
    code_gates = phase(5, PhaseName.CODE_GATES, PhaseKind.CODE, "orchestrator")
    phase(6, PhaseName.COMMIT, PhaseKind.CODE, "orchestrator")
    review = phase(
        7,
        PhaseName.REVIEW,
        PhaseKind.AGENT,
        "reviewer",
        harness="codex",
        version="0.112.0",
        model="gpt-5-codex",
        session="rev-ok",
        tokens=(15230, 1480, 9000, 0),
        cost=0.2,
    )

    def gate(
        phase_id: str,
        name: GateName,
        *,
        passed: bool,
        attempt: int = 1,
        violations: list[dict[str, str]] | None = None,
        command: str | None = None,
        exit_code: int | None = None,
        output_tail: str | None = None,
    ) -> None:
        writer.write_gate_result(
            run_id=run_id,
            phase_id=phase_id,
            attempt=attempt,
            gate=name,
            passed=passed,
            checks=[{"item": "checked", "ok": passed, "note": ""}],
            violations=violations,
            command=command,
            exit_code=exit_code,
            output_tail=output_tail,
        )

    for name in (GateName.SPEC_SECTIONS, GateName.SPEC_EARS, GateName.SPEC_AC_HOLDOUT_MAP):
        gate(spec_gate, name, passed=True)
    gate(code_gates, GateName.ENVELOPE_SCHEMA, passed=True)
    gate(code_gates, GateName.PERMISSIONS_ENFORCE, passed=True)
    gate(
        code_gates,
        GateName.FOCUSED_TESTS,
        passed=False,
        violations=[
            {
                "item": "tests/unit/test_invoice_due_date.py::test_zero_terms",
                "problem": "assert 1 == 0 | left side",
            }
        ],
        command="uv run pytest -q tests/unit/test_invoice_due_date.py",
        exit_code=1,
        output_tail="FAILED ... assert 1 == 0\n",
    )
    gate(
        code_gates,
        GateName.FOCUSED_TESTS,
        passed=True,
        attempt=2,
        command="uv run pytest -q tests/unit/test_invoice_due_date.py",
        exit_code=0,
    )
    writer.write_event(
        EventKind.LOG,
        "orchestrator",
        task_id=TASK,
        run_id=run_id,
        phase_id=review,
        payload={
            "verdict_policy": {
                "verdict": "accepted",
                "conformance": 94.0,
                "strongest": {
                    "requirement_id": "style",
                    "equation": "30 x 0.5 x 0.4 x 1 = 6",
                    "score": 6.0,
                },
                "findings": [
                    {
                        "requirement_id": "style",
                        "severity": "Minor",
                        "location": None,
                        "problem": "Duplicated date arithmetic (ask @mlima).",
                        "score": 6.0,
                    }
                ],
                "dismissed": [
                    {"requirement_id": "performance", "dismissed_by": "Caching of invoice queries."}
                ],
                "missing_ac": [],
                "revisions_so_far": 0,
            }
        },
    )
    return run_id


def body_for(writer: TraceWriter) -> tuple[dict[str, object], str]:
    run_id = seed_trace(writer)
    fields = pr_body_fields(
        writer,
        task_id=TASK,
        run_id=run_id,
        spec_rel=SPEC_REL,
        spec_text=SPEC_WITH_MENTION,
        branch="agent/418",
        sha=SHA,
    )
    return fields, render_pr_body(fields)


def write_snapshot(writer: TraceWriter) -> None:
    """Regenerate the snapshot after a deliberate layout change."""
    _, body = body_for(writer)
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(body + "\n", encoding="utf-8", newline="\n")


def test_body_matches_the_snapshot(trace_writer: TraceWriter) -> None:
    _, body = body_for(trace_writer)
    assert SNAPSHOT.is_file(), f"missing {SNAPSHOT}; run write_snapshot() after a layout change"
    assert body == SNAPSHOT.read_text(encoding="utf-8").rstrip("\n")


def test_fields_are_exactly_the_declared_ones(trace_writer: TraceWriter) -> None:
    fields, body = body_for(trace_writer)
    assert set(fields) == template_fields("pr_body.md.j2")
    with pytest.raises(ValueError, match="declares no field"):
        render_pr_body({**fields, "transcript": "agent text"})
    assert "`@rsouza`" in body and "`@mlima`" in body and "ask @mlima" not in body
    assert "| focused_tests | 1 | fail |" in body and "assert 1 == 0 \\| left side" in body
    assert (
        "| focused_tests | 2 | pass |"
        " `uv run pytest -q tests/unit/test_invoice_due_date.py` exit 0 |" in body
    )
    assert (
        "| review | reviewer | codex | gpt-5-codex | 1 | pass |"
        " 15230 / 1480 / 9000 / 0 | 0.2000 | rev-ok |" in body
    )
    assert "Total cost: $0.8200 over 7 phase rows; tokens in 41680, out 4590." in body
    assert "| reviewer | codex | 0.112.0 | gpt-5-codex |" in body
    assert 'performance: "Caching of invoice queries."' in body
    assert "verdict `accepted`, conformance 94.0; strongest finding style" in body
    assert "no agent wrote this text" in body


def test_gate_evidence_shapes(trace_writer: TraceWriter) -> None:
    run_id = seed_trace(trace_writer)
    rows = trace_writer.gate_results_for_run(run_id)
    assert gate_evidence(rows[0]) == "1 check(s)"
    failed = next(r for r in rows if not r["passed"])
    assert gate_evidence(failed).startswith(
        "tests/unit/test_invoice_due_date.py::test_zero_terms: assert 1 == 0"
    )
    assert gate_evidence(failed).endswith("exit 1")
