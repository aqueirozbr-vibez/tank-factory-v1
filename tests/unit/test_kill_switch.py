"""Unit 40: the kill-switch flag, its fan-out, the re-enable control, the API and the CLI."""

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

from cli.main import main
from orchestrator.harness.process import run_with_timeout
from orchestrator.intake.scheduler import Scheduler
from orchestrator.models.enums import (
    PhaseKind,
    PhaseName,
    ProcessKind,
    TaskStatus,
    WorkspaceKind,
)
from orchestrator.models.settings import Settings
from orchestrator.phases.recorder import PhaseRecorder
from orchestrator.supervisor import Supervisor
from orchestrator.trace import TraceDB, TraceWriter
from orchestrator.watchdogs import KillSwitch
from tests.conftest import DEFAULT_VENDORS
from tests.unit.test_build_phase import GREEN
from tests.unit.test_container import FakeDocker
from tests.unit.test_review_phase import ACCEPTED
from tests.unit.test_runner import PLAN, Stack, prepare
from tests.unit.test_spec_gate import TASK

SLEEP = [sys.executable, "-c", "import time; time.sleep(30)"]


class Comments:
    def __init__(self) -> None:
        self.items: list[tuple[int, str]] = []

    async def create_comment(self, number: int, body: str, *, task_id: str) -> None:
        self.items.append((number, body))


def admit(writer: TraceWriter, issue: int, *, status: TaskStatus = TaskStatus.RUNNING) -> str:
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
        status=status,
    )
    return task_id


def live_phase(writer: TraceWriter, tmp_path: Path) -> Supervisor:
    """A supervisor whose tasks each open a run, a phase and a sleeping process."""

    async def runner(task_id: str) -> None:
        run_id = writer.start_run(
            task_id=task_id, attempt=1, factory_version="0.1.0", config_sha="cfg1"
        )
        recorder = PhaseRecorder(writer, task_id=task_id, run_id=run_id)
        phase_id = recorder.start(
            PhaseName.BUILD, PhaseKind.AGENT, "builder", attempt=1, timeout_s=600
        )
        process_id = writer.start_process(
            run_id=run_id, phase_id=phase_id, kind=ProcessKind.AGENT, name="fake"
        )
        await run_with_timeout(
            SLEEP,
            cwd=tmp_path,
            env=Settings.subprocess_base_env(),
            timeout_s=120,
            on_process=lambda pid: writer.set_process_pid(process_id, pid),
        )

    return Supervisor(runner)


async def wait_for_pids(writer: TraceWriter, count: int) -> None:
    for _ in range(200):
        if (
            int(writer.db.scalar("SELECT COUNT(*) FROM processes WHERE pid IS NOT NULL") or 0)
            >= count
        ):
            return
        await asyncio.sleep(0.05)
    raise AssertionError("the sleeping processes never reported their pids")


def controls(writer: TraceWriter) -> list[dict[str, object]]:
    rows = writer.db.fetchall(
        "SELECT actor, payload_json FROM events WHERE kind = 'control' ORDER BY seq"
    )
    return [{"actor": r["actor"], **json.loads(r["payload_json"])} for r in rows]


def test_flag_reads_fail_closed(trace_writer: TraceWriter, tmp_path: Path) -> None:
    assert trace_writer.factory_enabled() is True
    trace_writer.set_factory_enabled(False)
    assert trace_writer.factory_enabled() is False
    bare = TraceDB.open(tmp_path / "bare.db")
    try:
        assert TraceWriter(bare).factory_enabled() is False  # no installation row counts as 0
    finally:
        bare.close()


async def test_stop_ends_three_live_phases_within_the_bound(
    trace_writer: TraceWriter, tmp_path: Path
) -> None:
    ids = [admit(trace_writer, issue) for issue in (418, 419, 420)]
    supervisor = live_phase(trace_writer, tmp_path)
    for task_id in ids:
        supervisor.start(task_id)
    await wait_for_pids(trace_writer, 3)
    assert trace_writer.count_open_processes() == 3
    github = Comments()
    switch = KillSwitch(trace_writer, supervisor=supervisor, github=github)
    started = time.monotonic()
    report = await switch.disable(actor="rsouza", reason="incident 7: stop everything")
    elapsed = time.monotonic() - started
    assert elapsed < 60, elapsed
    assert supervisor.live == [] and report.live_after == 0 and report.enabled is False
    assert report.cancelled == tuple(ids) and report.containers == ()
    assert trace_writer.factory_enabled() is False
    assert trace_writer.count_open_processes() == 0
    tasks = trace_writer.db.fetchall("SELECT task_id, status FROM tasks ORDER BY task_id")
    assert [(t["task_id"], t["status"]) for t in tasks] == [(i, "cancelled") for i in ids]
    phases = trace_writer.db.fetchall("SELECT status, error FROM phases")
    assert [(p["status"], p["error"]) for p in phases] == [("fail", "kill_switch")] * 3
    recorded = controls(trace_writer)
    assert len(recorded) == 1 and recorded[0]["actor"] == "rsouza"
    assert recorded[0]["action"] == "kill_switch" and recorded[0]["enabled"] == 0
    assert recorded[0]["reason"] == "incident 7: stop everything"
    assert recorded[0]["cancelled"] == ids and report.event_seq is not None
    assert sorted(n for n, _ in github.items) == [418, 419, 420]
    assert all("the factory was stopped" in body for _, body in github.items)
    assert all("before any phase completed" in body for _, body in github.items)

    again = await switch.enable(actor="rsouza", reason="root cause fixed")
    assert again.enabled is True and trace_writer.factory_enabled() is True
    recorded = controls(trace_writer)
    assert len(recorded) == 2 and recorded[1]["enabled"] == 1
    assert recorded[1]["reason"] == "root cause fixed"
    tasks = trace_writer.db.fetchall("SELECT status FROM tasks")
    assert {t["status"] for t in tasks} == {"cancelled"}  # re-enabling re-admits nothing


async def test_watchdog_loop_fans_out_a_flag_written_elsewhere(
    trace_writer: TraceWriter, tmp_path: Path
) -> None:
    task_id = admit(trace_writer, 418)
    supervisor = live_phase(trace_writer, tmp_path)
    supervisor.start(task_id)
    await wait_for_pids(trace_writer, 1)
    switch = KillSwitch(trace_writer, supervisor=supervisor, github=Comments(), interval_s=0.1)
    stop = asyncio.Event()
    loop = asyncio.create_task(switch.run(stop))
    trace_writer.set_factory_enabled(False)  # as `factory stop` does from another process
    for _ in range(400):
        if not supervisor.live:
            break
        await asyncio.sleep(0.05)
    stop.set()
    await loop
    assert supervisor.live == []
    task = trace_writer.get_task(task_id)
    assert task is not None and task["status"] == "cancelled"
    assert controls(trace_writer) == []  # the fan-out logs; the control was the caller's
    log = trace_writer.db.fetchone(
        "SELECT payload_json FROM events WHERE kind = 'log' AND payload_json LIKE '%fan_out%'"
    )
    assert log is not None


async def test_phase_start_is_refused_while_disabled(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    prepare(product, tmp_path)
    stack = Stack(trace_writer, product, tmp_path, [PLAN, GREEN, ACCEPTED])
    stack.admit()
    trace_writer.set_factory_enabled(False)
    report = await stack.runner.run(TASK)
    assert report.status == "cancelled" and stack.phases(report.run_id) == []
    assert stack.task()["status"] == "cancelled"
    assert stack.harness.calls == []
    run = trace_writer.get_run(report.run_id)
    assert run is not None and run["status"] == "fail"
    assert any("the factory was stopped" in c for c in stack.github.comments())


async def test_scheduler_dispatches_nothing_while_disabled(trace_writer: TraceWriter) -> None:
    task_id = admit(trace_writer, 418, status=TaskStatus.QUEUED)

    async def noop(_task_id: str) -> None:
        return None

    scheduler = Scheduler(trace_writer, Supervisor(noop))
    trace_writer.set_factory_enabled(False)
    assert await scheduler.dispatch() == []
    trace_writer.set_factory_enabled(True)
    assert await scheduler.dispatch() == [task_id]


async def test_labelled_containers_are_killed(trace_writer: TraceWriter) -> None:
    docker = FakeDocker()
    docker.containers["factory-task-7-1"] = "running"
    switch = KillSwitch(trace_writer, docker=docker)
    report = await switch.disable(actor="ops", reason="drill")
    assert report.containers == ("factory-task-7-1",)
    assert ["kill", "--signal", "TERM", "factory-task-7-1"] in docker.calls
    assert ["rm", "-f", "factory-task-7-1"] in docker.calls and docker.containers == {}


def test_cli_stop_and_start_without_an_orchestrator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db = TraceDB.open(data_dir / "trace.db")
    writer = TraceWriter(db)
    writer.load_config_version(
        config_sha="cfg1",
        main_commit="abc",
        roster_json="{}",
        risk_yaml="",
        review_policy_yaml="",
        protected_paths_json="[]",
        quality_commands_json="{}",
        vendors_json=DEFAULT_VENDORS,
    )
    writer.insert_installation(
        org="acme",
        repo="atlas-api",
        factory_version="0.1.0",
        config_sha="cfg1",
        per_task_budget_usd=6.0,
        export_pubkey="pk",
    )
    db.close()
    monkeypatch.setenv("FACTORY_BIND", "127.0.0.1")
    monkeypatch.setenv("FACTORY_PORT", "1")  # nothing listens: the direct path
    args = ["--reason", "drill", "--by", "ops", "--data-dir", str(data_dir)]
    assert main(["stop", *args]) == 0
    assert "stopped by ops" in capsys.readouterr().out
    assert main(["stop", *args]) == 0
    assert "already stopped" in capsys.readouterr().out
    assert main(["start", "--reason", "drill over", "--data-dir", str(data_dir)]) == 0
    assert main(["start", "--reason", "", "--data-dir", str(data_dir)]) == 2

    db = TraceDB.open(data_dir / "trace.db")
    try:
        writer = TraceWriter(db)
        assert writer.factory_enabled() is True
        recorded = controls(writer)
        first = recorded[0]
        assert (first["actor"], first["enabled"], first["reason"]) == ("ops", 0, "drill")
        assert recorded[1]["enabled"] == 1 and recorded[1]["reason"] == "drill over"
        assert len(recorded) == 2
    finally:
        db.close()
