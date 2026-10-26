"""Unit 49: the budget switches to 2x the rolling median on day 8 and recomputes daily."""

import asyncio
from datetime import UTC, datetime, timedelta

from orchestrator.budget.median import MedianBudget, measured_costs
from orchestrator.models.enums import (
    BlockedReason,
    PhaseKind,
    PhaseName,
    PhaseStatus,
    RunStatus,
    TaskStatus,
    WorkspaceKind,
)
from orchestrator.trace import TraceWriter

T0 = datetime.now(UTC)  # the fixture's installation row is created now
DAY = timedelta(days=1)


def measured(
    writer: TraceWriter,
    issue: int,
    *,
    spend: float,
    status: TaskStatus = TaskStatus.MERGED,
) -> str:
    """A task admitted now whose one build phase cost ``spend``, left in ``status``."""
    task_id = f"acme/atlas-api#{issue}"
    writer.insert_task(
        task_id=task_id,
        issue_number=issue,
        title=f"task {issue}",
        branch=f"agent/{issue}",
        workspace_kind=WorkspaceKind.WORKTREE,
        budget_usd=6.0,
        admitted_by="rsouza",
        config_sha="cfg1",
    )
    run_id = writer.start_run(
        task_id=task_id, attempt=1, factory_version="0.1.0", config_sha="cfg1"
    )
    phase_id = writer.start_phase(
        run_id=run_id,
        seq=1,
        name=PhaseName.BUILD,
        kind=PhaseKind.AGENT,
        owner="builder",
        timeout_s=60,
    )
    writer.finish_phase(phase_id, PhaseStatus.PASS, cost_usd=spend)
    writer.finish_run(run_id, RunStatus.SUCCESS)
    reason = BlockedReason.REPAIR_CAP if status is TaskStatus.BLOCKED else None
    writer.set_task_status(task_id, status, blocked_reason=reason)
    return task_id


def installation(writer: TraceWriter) -> tuple[str, float, float]:
    row = writer.get_installation()
    assert row is not None
    return str(row["budget_mode"]), float(row["per_task_budget_usd"]), float(row["daily_cap_usd"])


def test_switches_on_day_eight_and_recomputes_daily(trace_writer: TraceWriter) -> None:
    w = trace_writer
    budget = MedianBudget(w, clock=lambda: T0)
    assert installation(w) == ("install", 6.0, 54.0)

    measured(w, 1, spend=1.0)
    assert budget.refresh(now=T0 + 6 * DAY).code == "install_period"
    assert installation(w) == ("install", 6.0, 54.0)  # the first week keeps the install value

    measured(w, 2, spend=2.0, status=TaskStatus.PR_OPEN)
    measured(w, 3, spend=4.0, status=TaskStatus.BLOCKED)
    measured(w, 4, spend=9.0, status=TaskStatus.RUNNING)  # a partial spend: not a measurement
    measured(w, 5, spend=0.0, status=TaskStatus.CANCELLED)  # nothing spent: not a measurement
    assert sorted(measured_costs(w, since="2000-01-01T00:00:00.000+00:00")) == [1.0, 2.0, 4.0]

    day8 = budget.refresh(now=T0 + 8 * DAY)
    assert (day8.code, day8.median_usd, day8.per_task_budget_usd, day8.tasks) == (
        "switched",
        2.0,
        4.0,
        3,
    )
    assert day8.previous_usd == 6.0 and day8.event_seq is not None and day8.changed
    assert installation(w) == ("median", 4.0, 36.0)  # the daily cap followed by trigger: 3 x 3 x 4
    assert budget.refresh(now=T0 + 8 * DAY).code == "unchanged"  # the same day: nothing to write

    measured(w, 6, spend=8.0)
    day9 = budget.refresh(now=T0 + 9 * DAY)
    assert (day9.code, day9.median_usd, day9.per_task_budget_usd) == ("recomputed", 3.0, 6.0)

    with w.db.transaction() as conn:  # two early tasks fall out of the 30-day window
        old = (T0 - 25 * DAY).isoformat(timespec="milliseconds")
        conn.execute("UPDATE tasks SET created_at = ? WHERE issue_number IN (1, 2)", (old,))
    day10 = budget.refresh(now=T0 + 10 * DAY)
    assert (day10.code, day10.median_usd, day10.per_task_budget_usd, day10.tasks) == (
        "recomputed",
        6.0,
        12.0,
        2,
    )
    controls = w.db.fetchall(
        "SELECT payload_json FROM events WHERE kind = 'control' AND task_id IS NULL ORDER BY seq"
    )
    assert len(controls) == 3 and all('"action":"budget_median"' in c[0] for c in controls)
    assert '"mode_before":"install"' in controls[0][0]


def test_no_measurement_keeps_the_install_value(trace_writer: TraceWriter) -> None:
    budget = MedianBudget(trace_writer, clock=lambda: T0 + 8 * DAY)
    outcome = budget.refresh()
    assert (outcome.code, outcome.per_task_budget_usd) == ("no_measurements", 6.0)
    assert installation(trace_writer)[0] == "install"


async def test_daily_loop_refreshes_on_the_day_change(trace_writer: TraceWriter) -> None:
    w = trace_writer
    measured(w, 1, spend=1.0)
    moment = [T0 + 8 * DAY]
    budget = MedianBudget(w, clock=lambda: moment[0])
    stop = asyncio.Event()
    loop = asyncio.create_task(budget.run(stop, interval_s=0.05))
    await asyncio.sleep(0.2)
    assert installation(w) == ("median", 2.0, 18.0)
    measured(w, 2, spend=3.0)
    await asyncio.sleep(0.2)
    assert installation(w)[1] == 2.0  # the same day: no recomputation
    moment[0] = T0 + 9 * DAY
    await asyncio.sleep(0.3)
    assert installation(w) == ("median", 4.0, 36.0)  # median of 1.0 and 3.0 is 2.0; 2x
    stop.set()
    await loop
