"""Unit 14: budget check before each agent call and the stuck-run watchdog."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from orchestrator.budget import check_budget
from orchestrator.budget.check import day_start_utc
from orchestrator.harness.fake import FakeHarness
from orchestrator.issue_to_pr import run_build_slice
from orchestrator.models.enums import (
    BlockedReason,
    PhaseKind,
    PhaseName,
    PhaseStatus,
    ProcessKind,
    WorkspaceKind,
)
from orchestrator.models.settings import Settings
from orchestrator.phases.build import BuildDeps
from orchestrator.trace import TraceWriter, verify_chain
from orchestrator.watchdogs import Watchdog, find_stuck
from orchestrator.workspace.worktree import create_worktree
from tests.unit.test_build_phase import GREEN, SPEC_REL, TASK_ID, make_config


def _admit(trace_writer: TraceWriter, *, budget_usd: float) -> BuildDeps:
    cfg = make_config()
    trace_writer.load_config_version(**cfg.config_version_row())
    trace_writer.insert_task(
        task_id=TASK_ID,
        issue_number=418,
        title="Invoice due date",
        branch="agent/418",
        workspace_kind=WorkspaceKind.WORKTREE,
        budget_usd=budget_usd,
        admitted_by="rsouza",
        config_sha=cfg.config_sha,
    )
    return BuildDeps(
        writer=trace_writer,
        config=cfg,
        settings=Settings(),
        harness=FakeHarness([GREEN]),
        factory_version="0.1.0-test",
    )


def _other_task_spend(trace_writer: TraceWriter, cost: float) -> None:
    trace_writer.insert_task(
        task_id="acme/atlas-api#1",
        issue_number=1,
        title=None,
        branch="agent/1",
        workspace_kind=WorkspaceKind.WORKTREE,
        budget_usd=100.0,
        admitted_by="rsouza",
        config_sha="cfg1",
    )
    run_id = trace_writer.start_run(
        task_id="acme/atlas-api#1", attempt=1, factory_version="0", config_sha="cfg1"
    )
    phase_id = trace_writer.start_phase(
        run_id=run_id,
        seq=1,
        name=PhaseName.BUILD,
        kind=PhaseKind.AGENT,
        owner="builder",
        timeout_s=10,
    )
    trace_writer.finish_phase(phase_id, PhaseStatus.PASS, cost_usd=cost)


def test_day_start_in_installation_timezone() -> None:
    now = datetime(2026, 10, 24, 1, 30, tzinfo=UTC)  # 22:30 the day before in São Paulo
    assert day_start_utc(now, "America/Sao_Paulo") == "2026-10-23T03:00:00.000+00:00"
    assert day_start_utc(now, "UTC") == "2026-10-24T00:00:00.000+00:00"


def test_check_budget_decisions(trace_writer: TraceWriter) -> None:
    _admit(trace_writer, budget_usd=6.0)
    ok = check_budget(trace_writer, task_id=TASK_ID, phase_cap_usd=3.0)
    assert ok.ok and ok.reason is None
    assert (ok.task_budget, ok.daily_cap) == (6.0, 54.0)

    _other_task_spend(trace_writer, 52.0)
    daily = check_budget(trace_writer, task_id=TASK_ID, phase_cap_usd=3.0)
    assert not daily.ok and daily.reason is not None and daily.reason.startswith("daily cap")
    assert daily.daily_spent == pytest.approx(52.0)
    assert daily.payload()["daily_cap_usd"] == 54.0


async def test_task_budget_blocks_before_the_first_call(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    deps = _admit(trace_writer, budget_usd=1.0)
    wt = create_worktree(product, issue=418, worktrees_dir=tmp_path / "w")
    outcome = await run_build_slice(deps, task_id=TASK_ID, worktree=wt, spec_rel=SPEC_REL)
    assert outcome.blocked_reason is BlockedReason.BUDGET
    assert outcome.builder_calls == 0
    assert "task budget" in outcome.evidence
    stop = trace_writer.db.fetchone("SELECT payload_json FROM events WHERE kind = 'budget_stop'")
    assert stop is not None
    payload = json.loads(stop["payload_json"])
    assert (payload["task_budget_usd"], payload["phase_cap_usd"]) == (1.0, 3.0)
    task = trace_writer.get_task(TASK_ID)
    assert task is not None and task["blocked_reason"] == "budget"
    assert verify_chain(trace_writer.db).ok


async def test_daily_cap_blocks(product: Path, tmp_path: Path, trace_writer: TraceWriter) -> None:
    deps = _admit(trace_writer, budget_usd=6.0)
    _other_task_spend(trace_writer, 52.0)
    wt = create_worktree(product, issue=418, worktrees_dir=tmp_path / "w")
    outcome = await run_build_slice(deps, task_id=TASK_ID, worktree=wt, spec_rel=SPEC_REL)
    assert outcome.blocked_reason is BlockedReason.BUDGET and outcome.builder_calls == 0
    assert "daily cap" in outcome.evidence


async def test_budget_that_fits_still_passes(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    deps = _admit(trace_writer, budget_usd=6.0)
    wt = create_worktree(product, issue=418, worktrees_dir=tmp_path / "w")
    outcome = await run_build_slice(deps, task_id=TASK_ID, worktree=wt, spec_rel=SPEC_REL)
    assert outcome.passed


def _process(trace_writer: TraceWriter, *, idle: timedelta, ended: bool = False) -> str:
    task_id = f"acme/atlas-api#{trace_writer.db.scalar('SELECT COUNT(*) FROM tasks') + 10}"
    trace_writer.insert_task(
        task_id=task_id,
        issue_number=int(task_id.rsplit("#", 1)[1]),
        title=None,
        branch="agent/x",
        workspace_kind=WorkspaceKind.WORKTREE,
        budget_usd=6.0,
        admitted_by="rsouza",
        config_sha="cfg1",
    )
    run_id = trace_writer.start_run(
        task_id=task_id, attempt=1, factory_version="0", config_sha="cfg1"
    )
    phase_id = trace_writer.start_phase(
        run_id=run_id,
        seq=1,
        name=PhaseName.BUILD,
        kind=PhaseKind.AGENT,
        owner="builder",
        timeout_s=10,
    )
    process_id = trace_writer.start_process(
        run_id=run_id, phase_id=phase_id, kind=ProcessKind.AGENT, name="claude"
    )
    last = (datetime.now(UTC) - idle).isoformat(timespec="milliseconds")
    with trace_writer.db.transaction() as conn:
        conn.execute("UPDATE processes SET last_event_at = ? WHERE id = ?", (last, process_id))
    if ended:
        trace_writer.end_process(process_id, exit_code=0)
    return process_id


def test_find_stuck_and_watchdog_flags_once(trace_writer: TraceWriter) -> None:
    stuck_id = _process(trace_writer, idle=timedelta(minutes=16))
    _process(trace_writer, idle=timedelta(minutes=5))
    _process(trace_writer, idle=timedelta(minutes=40), ended=True)
    found = find_stuck(trace_writer.db)
    assert [p.id for p in found] == [stuck_id]
    assert found[0].idle_s > 15 * 60

    watchdog = Watchdog(trace_writer)
    first = watchdog.tick()
    second = watchdog.tick()
    assert [p.id for p in first] == [stuck_id] and second == []
    rows = trace_writer.db.fetchall("SELECT payload_json FROM events WHERE kind = 'log'")
    payloads = [json.loads(r["payload_json"]) for r in rows]
    assert len(payloads) == 1 and payloads[0]["stuck"] is True
    assert payloads[0]["process_id"] == stuck_id and payloads[0]["threshold_s"] == 900


async def test_watchdog_run_stops_on_event(trace_writer: TraceWriter) -> None:
    watchdog = Watchdog(trace_writer, interval_s=0.01)
    stop = asyncio.Event()
    task = asyncio.create_task(watchdog.run(stop))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, 2)
    assert task.done()
