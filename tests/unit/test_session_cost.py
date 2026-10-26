"""A resumed Claude Code session reports its running total; the trace books each call's part once.

On 2026-10-25 three infra retries of one build resumed the same session after the API refused
("Credit balance is too low"); each reported the session's US$0.56 again and the trace booked
US$2.24 for US$0.56 of spend.
"""

from orchestrator.harness.base import HarnessResult, Usage
from orchestrator.models.enums import PhaseKind, PhaseName, PhaseStatus
from orchestrator.phases.recorder import PhaseRecorder
from orchestrator.trace import TraceWriter
from tests.unit.test_kill_switch import admit


def result(cost: float, *, session: str | None = "s-1", total: bool = True) -> HarnessResult:
    return HarnessResult(
        harness="claude_code",
        harness_version="2.1.310",
        outcome="completed",
        session_id=session,
        usage=Usage(cost_usd=cost),
        cost_is_session_total=total,
    )


def phase(recorder: PhaseRecorder, outcome: HarnessResult) -> None:
    phase_id = recorder.start(PhaseName.BUILD, PhaseKind.AGENT, "builder", attempt=1, timeout_s=60)
    recorder.finish(phase_id, PhaseStatus.FAIL, result=outcome)


def costs(writer: TraceWriter, run_id: str) -> list[float]:
    rows = writer.db.fetchall(
        "SELECT cost_usd FROM phases WHERE run_id = ? ORDER BY seq", (run_id,)
    )
    return [round(float(r["cost_usd"]), 4) for r in rows]


def test_a_resumed_session_books_only_the_new_part(trace_writer: TraceWriter) -> None:
    task = admit(trace_writer, 4)
    run = trace_writer.start_run(
        task_id=task, attempt=1, factory_version="0.1.0", config_sha="cfg1"
    )
    recorder = PhaseRecorder(trace_writer, task_id=task, run_id=run)
    for reported in (0.558, 0.558, 0.558, 0.80):  # three refused retries, then a call that worked
        phase(recorder, result(reported))
    assert costs(trace_writer, run) == [0.558, 0.0, 0.0, 0.242]
    assert trace_writer.session_cost_usd("s-1") == 0.8
    spent = trace_writer.db.fetchone("SELECT spent_usd FROM tasks WHERE task_id = ?", (task,))
    assert spent is not None and round(float(spent["spent_usd"]), 4) == 0.8


def test_per_call_costs_and_new_sessions_are_booked_as_reported(trace_writer: TraceWriter) -> None:
    task = admit(trace_writer, 5)
    run = trace_writer.start_run(
        task_id=task, attempt=1, factory_version="0.1.0", config_sha="cfg1"
    )
    recorder = PhaseRecorder(trace_writer, task_id=task, run_id=run)
    phase(recorder, result(0.30, session="a"))
    phase(recorder, result(0.20, session="b"))  # a fresh session starts from zero
    phase(recorder, result(0.25, session="a", total=False))  # a per-call harness: as reported
    phase(recorder, result(0.10, session=None))  # no session id: nothing to subtract
    assert costs(trace_writer, run) == [0.30, 0.20, 0.25, 0.10]
