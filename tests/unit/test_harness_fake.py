"""Unit 05: the fake harness and the trace sink."""

import json
from pathlib import Path

import pytest

from orchestrator.errors import HarnessError
from orchestrator.harness import (
    FakeHarness,
    FakeScript,
    HarnessRequest,
    ToolCall,
    trace_tool_call_sink,
)
from orchestrator.harness.fake import FakeEffect, apply_effects
from orchestrator.models.enums import (
    AgentRole,
    PhaseKind,
    PhaseName,
    ProcessKind,
    WorkspaceKind,
)
from orchestrator.models.settings import Settings
from orchestrator.trace import TraceWriter, verify_chain

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "envelopes"


def _request(cwd: Path, *, timeout_s: int = 30, resume: str | None = None) -> HarnessRequest:
    return HarnessRequest(
        role=AgentRole.BUILDER,
        prompt="Implement the spec.",
        output_schema={"type": "object"},
        cwd=cwd,
        env=Settings().subprocess_base_env(),
        model="fake-1",
        timeout_s=timeout_s,
        tools=("Read", "Edit", "Bash"),
        max_turns=80,
        budget_usd=3.0,
        resume_session_id=resume,
    )


async def test_green_fixture_replays(tmp_path: Path) -> None:
    harness = FakeHarness.from_fixtures(FIXTURES / "builder_green.json")
    calls: list[ToolCall] = []
    result = await harness.run(_request(tmp_path), on_tool_call=calls.append)
    assert result.completed and result.harness == "fake"
    assert result.session_id == "fake-builder-0001"
    assert result.usage.cost_usd == pytest.approx(0.41)
    assert result.usage.input_tokens == 18450
    assert [c.tool for c in calls] == ["Read", "Edit", "Bash"]
    assert calls[0].tool_call_id == "fake-builder-0001-1"
    assert calls[0].started_at is not None and calls[0].ended_at is not None
    assert result.envelope_text is not None
    envelope = json.loads(result.envelope_text)
    assert envelope["changed_files"] == [
        "src/billing/invoice.py",
        "tests/unit/test_invoice_due_date.py",
    ]
    assert (
        (tmp_path / "src" / "billing" / "invoice.py")
        .read_text(encoding="utf-8")
        .startswith("from datetime import date")
    )
    assert (tmp_path / "tests" / "unit" / "test_invoice_due_date.py").is_file()
    assert harness.calls[0].prompt == "Implement the spec."


async def test_scripts_are_consumed_in_order_and_the_last_repeats(tmp_path: Path) -> None:
    harness = FakeHarness(
        [
            FakeScript(session_id="s1", envelope={"status": "fail"}),
            FakeScript(session_id="s2", envelope={"status": "success"}),
        ]
    )
    first = await harness.run(_request(tmp_path), on_tool_call=lambda _: None)
    second = await harness.run(_request(tmp_path), on_tool_call=lambda _: None)
    third = await harness.run(_request(tmp_path), on_tool_call=lambda _: None)
    assert [first.session_id, second.session_id, third.session_id] == ["s1", "s2", "s2"]
    assert third.envelope_text == '{"status": "success"}'


async def test_resume_session_id_is_kept_when_the_script_has_none(tmp_path: Path) -> None:
    harness = FakeHarness([FakeScript(envelope={})])
    result = await harness.run(
        _request(tmp_path, resume="prior-session"), on_tool_call=lambda _: None
    )
    assert result.session_id == "prior-session"


def test_effects_stay_inside_the_workspace(tmp_path: Path) -> None:
    with pytest.raises(HarnessError, match="inside the workspace"):
        apply_effects(tmp_path, [FakeEffect(write="../escape.txt")])
    with pytest.raises(HarnessError, match="inside the workspace"):
        apply_effects(tmp_path, [FakeEffect(write="/etc/passwd")])
    with pytest.raises(HarnessError, match="inside the workspace"):
        apply_effects(tmp_path, [FakeEffect(write="C:/Windows/x")])
    touched = apply_effects(
        tmp_path, [FakeEffect(write="a/b.txt", content="x"), FakeEffect(delete="a/b.txt")]
    )
    assert touched == ["a/b.txt", "a/b.txt"]
    assert not (tmp_path / "a" / "b.txt").exists()


async def test_sleeping_script_times_out(tmp_path: Path) -> None:
    harness = FakeHarness([FakeScript(sleep_s=30, envelope={"status": "success"})])
    pids: list[int] = []
    result = await harness.run(
        _request(tmp_path, timeout_s=1), on_tool_call=lambda _: None, on_process=pids.append
    )
    assert result.outcome == "timeout"
    assert result.envelope_text is None
    assert result.error == "timed out after 1 s"
    assert len(pids) == 1


async def test_fail_with_reports_a_harness_error(tmp_path: Path) -> None:
    harness = FakeHarness([FakeScript(fail_with="binary not found")])
    result = await harness.run(_request(tmp_path), on_tool_call=lambda _: None)
    assert result.outcome == "error" and result.error == "binary not found"


def test_empty_script_list_is_refused() -> None:
    with pytest.raises(HarnessError):
        FakeHarness([])


async def test_tool_calls_land_in_the_trace(tmp_path: Path, trace_writer: TraceWriter) -> None:
    task_id = "acme/atlas-api#418"
    trace_writer.insert_task(
        task_id=task_id,
        issue_number=418,
        title="Invoice due date",
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
        timeout_s=7200,
        harness="fake",
    )
    process_id = trace_writer.start_process(
        run_id=run_id, phase_id=phase_id, kind=ProcessKind.AGENT, name="fake"
    )
    before = trace_writer.get_process(process_id)
    assert before is not None
    sink = trace_tool_call_sink(
        trace_writer,
        task_id=task_id,
        run_id=run_id,
        phase_id=phase_id,
        actor="builder",
        process_id=process_id,
    )
    harness = FakeHarness.from_fixtures(FIXTURES / "builder_green.json")
    await harness.run(_request(tmp_path), on_tool_call=sink)
    rows = trace_writer.db.fetchall(
        "SELECT kind, actor, payload_json, started_at FROM events WHERE phase_id = ? ORDER BY seq",
        (phase_id,),
    )
    assert [r["kind"] for r in rows] == ["tool_call"] * 3
    payloads = [json.loads(r["payload_json"]) for r in rows]
    assert [p["tool"] for p in payloads] == ["Read", "Edit", "Bash"]
    assert payloads[0]["agent"] == "builder"
    assert payloads[2]["result_snippet"] == "2 passed in 0.31s"
    assert all(r["started_at"] for r in rows)
    after = trace_writer.get_process(process_id)
    assert after is not None
    assert after["last_event_at"] >= before["last_event_at"]
    assert verify_chain(trace_writer.db).ok


def test_long_result_snippets_are_capped(trace_writer: TraceWriter) -> None:
    from orchestrator.models.enums import TaskStatus

    task_id = "acme/atlas-api#9"
    trace_writer.insert_task(
        task_id=task_id,
        issue_number=9,
        title=None,
        branch="agent/9",
        workspace_kind=WorkspaceKind.WORKTREE,
        budget_usd=1.0,
        admitted_by="rsouza",
        config_sha="cfg1",
        status=TaskStatus.RUNNING,
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
    sink = trace_tool_call_sink(
        trace_writer, task_id=task_id, run_id=run_id, phase_id=phase_id, actor="builder"
    )
    sink(ToolCall("Bash", "x-1", {"command": "ls"}, "y" * 5000, True, 5))
    payload = json.loads(
        trace_writer.db.scalar("SELECT payload_json FROM events WHERE phase_id = ?", (phase_id,))
    )
    assert len(payload["result_snippet"]) == 1000
