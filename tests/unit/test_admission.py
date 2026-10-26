"""Unit 20: router, admission, queue scheduler, cancel and process cancellation."""

import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from orchestrator.harness.process import run_with_timeout
from orchestrator.intake import Admission, Cancellation, Handlers, Router, Scheduler
from orchestrator.intake.router import touches_configuration
from orchestrator.models.enums import PhaseKind, PhaseName, PhaseStatus, TaskStatus
from orchestrator.models.events import NormalisedEvent
from orchestrator.models.settings import Settings
from orchestrator.supervisor import Supervisor
from orchestrator.trace import TraceWriter, verify_chain
from orchestrator.workspace.worktree import branch_exists
from tests.unit.test_process import base_env, pid_alive, wait_dead

TASK = "acme/atlas-api#418"


class FakeGitHub:
    def __init__(self, permissions: dict[str, str] | None = None) -> None:
        self.permissions = permissions or {"rsouza": "write", "reader": "read", "mlima": "admin"}
        self.calls: list[tuple[str, object]] = []
        self.permission_lookups = 0

    async def collaborator_permission(self, login: str, *, task_id: str) -> str:
        self.permission_lookups += 1
        return self.permissions.get(login, "none")

    async def get_issue(self, number: int, *, task_id: str) -> dict[str, Any]:
        self.calls.append(("get_issue", number))
        return {"number": number, "title": f"Issue {number}"}

    async def create_comment(self, number: int, body: str, *, task_id: str) -> int:
        self.calls.append(("comment", (number, body)))
        return len(self.calls)

    async def add_labels(self, number: int, labels: Sequence[str], *, task_id: str) -> None:
        self.calls.append(("add_labels", (number, tuple(labels))))

    async def remove_label(self, number: int, label: str, *, task_id: str) -> bool:
        self.calls.append(("remove_label", (number, label)))
        return True

    async def list_labels(self, number: int, *, task_id: str) -> list[str]:
        current: list[str] = []
        for kind, payload in self.calls:
            if kind == "add_labels" and payload[0] == number:  # type: ignore[index]
                current.extend(payload[1])  # type: ignore[index]
            if kind == "remove_label" and payload[0] == number:  # type: ignore[index]
                label = str(payload[1])  # type: ignore[index]
                if label in current:
                    current.remove(label)
        return current

    async def get_branch_sha(self, branch: str, *, task_id: str) -> str | None:
        return None

    def comments(self) -> list[str]:
        return [str(payload[1]) for kind, payload in self.calls if kind == "comment"]  # type: ignore[index]


def labeled(
    issue: int, label: str = "factory:ready", actor: str = "rsouza", delivery: str = "d"
) -> NormalisedEvent:
    return NormalisedEvent(
        kind="issue_labeled",
        source="webhook",
        github_event_id=f"{delivery}-{issue}-{label}",
        event_type="issues",
        action="labeled",
        issue_number=issue,
        actor_login=actor,
        label=label,
        received_at="2026-10-24T12:00:00.000+00:00",
    )


def unlabeled(issue: int, label: str = "factory:ready", actor: str = "rsouza") -> NormalisedEvent:
    return NormalisedEvent(
        kind="issue_unlabeled",
        source="webhook",
        github_event_id=f"u-{issue}-{label}",
        event_type="issues",
        action="unlabeled",
        issue_number=issue,
        actor_login=actor,
        label=label,
        received_at="2026-10-24T12:00:00.000+00:00",
    )


class BlockingRunner:
    """A task body that marks progress and waits until released or cancelled."""

    def __init__(self, writer: TraceWriter) -> None:
        self.writer = writer
        self.started: list[str] = []
        self.release = asyncio.Event()
        self.cancelled: list[str] = []

    async def __call__(self, task_id: str) -> None:
        self.started.append(task_id)
        row = self.writer.get_task(task_id)
        attempt = int(row["attempt"]) if row is not None else 1
        run_id = self.writer.start_run(
            task_id=task_id, attempt=attempt, factory_version="t", config_sha="cfg1"
        )
        phase = self.writer.start_phase(
            run_id=run_id,
            seq=1,
            name=PhaseName.ADMISSION,
            kind=PhaseKind.CODE,
            owner="orchestrator",
            timeout_s=10,
        )
        self.writer.finish_phase(phase, PhaseStatus.PASS)
        build = self.writer.start_phase(
            run_id=run_id,
            seq=2,
            name=PhaseName.BUILD,
            kind=PhaseKind.AGENT,
            owner="builder",
            timeout_s=10,
        )
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.append(task_id)
            raise
        self.writer.finish_phase(build, PhaseStatus.PASS)
        self.writer.set_task_status(task_id, TaskStatus.PR_OPEN)


def build_stack(
    trace_writer: TraceWriter,
    product: Path,
    tmp_path: Path,
    github: FakeGitHub,
    runner: BlockingRunner,
) -> tuple[Router, Admission, Cancellation, Scheduler, Supervisor]:
    scheduler_holder: dict[str, Scheduler] = {}
    supervisor = Supervisor(runner, on_finished=lambda tid: scheduler_holder["s"].on_finished(tid))
    scheduler = Scheduler(trace_writer, supervisor)
    scheduler_holder["s"] = scheduler
    settings = Settings(data_dir=tmp_path / "data")
    admission = Admission(
        writer=trace_writer,
        github=github,
        settings=settings,
        repo_clone=product,
        scheduler=scheduler,
    )
    cancellation = Cancellation(
        writer=trace_writer, github=github, supervisor=supervisor, scheduler=scheduler
    )
    router = Router(trace_writer, Handlers(admit=admission.admit, cancel=cancellation.cancel))
    return router, admission, cancellation, scheduler, supervisor


async def test_refused_actors_create_no_task(
    trace_writer: TraceWriter, product: Path, tmp_path: Path
) -> None:
    github = FakeGitHub()
    runner = BlockingRunner(trace_writer)
    _, admission, _, _, _ = build_stack(trace_writer, product, tmp_path, github, runner)
    result = await admission.admit(labeled(418, actor="reader"))
    assert result.outcome == "refused_actor" and result.permission == "read"
    bot = await admission.admit(labeled(418, actor="factory-app[bot]"))
    assert bot.outcome == "refused_actor" and bot.permission == "none"
    assert trace_writer.get_task(TASK) is None
    assert ("remove_label", (418, "factory:ready")) in github.calls
    assert any("write access" in c for c in github.comments())
    assert github.permission_lookups == 1  # the bot never reaches GitHub
    again = await admission.admit(labeled(418, actor="reader"))
    assert again.outcome == "refused_actor" and github.permission_lookups == 1  # cached
    assert runner.started == []


async def test_admission_is_idempotent_across_100_labels(
    trace_writer: TraceWriter, product: Path, tmp_path: Path
) -> None:
    github = FakeGitHub()
    runner = BlockingRunner(trace_writer)
    router, _, _, _, supervisor = build_stack(trace_writer, product, tmp_path, github, runner)
    outcomes = []
    for i in range(100):
        outcomes.append(await router.handle(labeled(418, delivery=f"d{i}")))
    await asyncio.sleep(0)
    assert outcomes == ["admit"] * 100
    assert trace_writer.db.scalar("SELECT COUNT(*) FROM tasks") == 1
    task = trace_writer.get_task(TASK)
    assert task is not None and task["status"] == "running" and task["title"] == "Issue 418"
    assert runner.started == [TASK]
    assert trace_writer.db.scalar("SELECT COUNT(*) FROM runs") == 1
    assert (tmp_path / "data" / "worktrees" / "agent-418").is_dir()
    duplicates = trace_writer.db.fetchall(
        "SELECT payload_json FROM events WHERE kind = 'log' AND payload_json LIKE '%duplicate%'"
    )
    assert len(duplicates) == 99
    assert len(github.comments()) == 0  # slot was free: no queue comment
    runner.release.set()
    await supervisor.wait(TASK)
    assert verify_chain(trace_writer.db).ok


async def test_queue_with_one_slot_and_promotion(
    trace_writer: TraceWriter, product: Path, tmp_path: Path
) -> None:
    trace_writer.set_concurrency(1)
    github = FakeGitHub()
    runner = BlockingRunner(trace_writer)
    _, admission, _, scheduler, supervisor = build_stack(
        trace_writer, product, tmp_path, github, runner
    )
    first = await admission.admit(labeled(418))
    second = await admission.admit(labeled(419))
    assert first.outcome == "admitted_running"
    assert second.outcome == "admitted_queued" and second.ahead == 0
    third = await admission.admit(labeled(420))
    assert third.outcome == "admitted_queued" and third.ahead == 1
    assert "1 task ahead" in github.comments()[-1]
    await asyncio.sleep(0)  # let the started task take its first step
    assert runner.started == [TASK]
    assert scheduler.slots_free() == 0
    runner.release.set()
    await supervisor.wait(TASK)
    await asyncio.sleep(0.1)  # each finish promotes the next; the release is shared
    assert runner.started == [TASK, "acme/atlas-api#419", "acme/atlas-api#420"]
    for issue in (418, 419, 420):
        row = trace_writer.get_task(f"acme/atlas-api#{issue}")
        assert row is not None and row["status"] == "pr_open"


async def test_cancel_live_queued_and_readmit(
    trace_writer: TraceWriter, product: Path, tmp_path: Path
) -> None:
    trace_writer.set_concurrency(1)
    github = FakeGitHub()
    runner = BlockingRunner(trace_writer)
    router, admission, cancellation, _, supervisor = build_stack(
        trace_writer, product, tmp_path, github, runner
    )
    await admission.admit(labeled(418))
    await admission.admit(labeled(419))
    await asyncio.sleep(0)

    assert await router.handle(unlabeled(418, actor="mlima")) == "cancel"
    assert runner.cancelled == [TASK] and not supervisor.is_live(TASK)
    task = trace_writer.get_task(TASK)
    assert task is not None and task["status"] == "cancelled"
    assert branch_exists(product, "agent/418")
    control = trace_writer.db.fetchone(
        "SELECT actor, payload_json FROM events WHERE kind = 'control'"
    )
    assert control is not None and control["actor"] == "mlima"
    payload = json.loads(control["payload_json"])
    assert payload["last_completed_phase"] == "admission" and payload["was_live"] is True
    assert "after phase `admission`" in github.comments()[-1]
    runs = trace_writer.db.fetchall("SELECT status FROM runs WHERE task_id = ?", (TASK,))
    assert [r["status"] for r in runs] == ["fail"]
    phases = trace_writer.db.fetchall(
        "SELECT p.name, p.status FROM phases p JOIN runs r ON r.run_id = p.run_id"
        " WHERE r.task_id = ?",
        (TASK,),
    )
    assert [(p["name"], p["status"]) for p in phases] == [("admission", "pass"), ("build", "fail")]

    await asyncio.sleep(0.05)  # the freed slot promotes 419
    assert trace_writer.get_task("acme/atlas-api#419")["status"] == "running"  # type: ignore[index]

    await admission.admit(labeled(420))
    assert await cancellation.cancel(unlabeled(420)) is True
    queued = trace_writer.get_task("acme/atlas-api#420")
    assert queued is not None and queued["status"] == "cancelled"
    assert "before any phase completed" in github.comments()[-1]
    assert await cancellation.cancel(unlabeled(420)) is False  # already cancelled

    readmit = await admission.admit(labeled(418, delivery="again"))
    assert readmit.outcome == "readmitted"
    task = trace_writer.get_task(TASK)
    assert task is not None and task["attempt"] == 2 and task["status"] == "queued"
    runner.release.set()
    await supervisor.wait("acme/atlas-api#419")


async def test_router_routes_and_logs_unhandled(
    trace_writer: TraceWriter, product: Path, tmp_path: Path
) -> None:
    github = FakeGitHub()
    runner = BlockingRunner(trace_writer)
    router, _, _, _, _ = build_stack(trace_writer, product, tmp_path, github, runner)
    merged = NormalisedEvent(
        kind="pull_request_merged",
        source="webhook",
        github_event_id="m-1",
        event_type="pull_request",
        action="closed",
        pr_number=431,
        branch="agent/418",
        received_at="2026-10-24T12:00:00.000+00:00",
    )
    trace_writer.record_github_event(
        source="webhook", event_type="pull_request", github_event_id="m-1", issue_number=None
    )
    assert await router.handle(merged) == "merged"
    assert await router.handle(labeled(418, label="bug")) == "ignored"
    row = trace_writer.db.fetchone(
        "SELECT processed_at FROM github_events WHERE github_event_id = 'm-1'"
    )
    assert row is not None and row["processed_at"] is not None
    logs = [
        json.loads(r["payload_json"])
        for r in trace_writer.db.fetchall("SELECT payload_json FROM events WHERE kind = 'log'")
    ]
    assert {(entry["router"], entry["route"]) for entry in logs} == {
        ("unhandled", "merged"),
        ("ignored", "ignored"),
    }
    assert touches_configuration((".factory/roster.yaml",)) and touches_configuration(
        ("services/uv.lock",)
    )
    assert not touches_configuration(("README.md",))


async def test_router_run_survives_a_failing_handler(trace_writer: TraceWriter) -> None:
    async def boom(event: NormalisedEvent) -> None:
        raise RuntimeError("handler exploded")

    async def noop(event: NormalisedEvent) -> None:
        return None

    router = Router(trace_writer, Handlers(admit=boom, cancel=noop))
    inbox: asyncio.Queue[NormalisedEvent] = asyncio.Queue()
    stop = asyncio.Event()
    task = asyncio.create_task(router.run(inbox, stop))
    inbox.put_nowait(labeled(1))
    await inbox.join()
    stop.set()
    await asyncio.wait_for(task, 3)
    error = trace_writer.db.fetchone("SELECT payload_json FROM events WHERE kind = 'error'")
    assert error is not None and "handler exploded" in error["payload_json"]


async def test_cancelled_run_with_timeout_kills_the_child(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    script = (
        "import os, pathlib, sys, time;"
        " pathlib.Path(sys.argv[1]).write_text(str(os.getpid()));"
        " print('ready', flush=True); time.sleep(120)"
    )
    pids: list[int] = []
    task = asyncio.create_task(
        run_with_timeout(
            [sys.executable, "-c", script, str(pid_file)],
            cwd=tmp_path,
            env=base_env(),
            timeout_s=60,
            on_process=pids.append,
            grace_s=0.5,
        )
    )
    for _ in range(100):
        await asyncio.sleep(0.05)
        if pid_file.exists() and pid_file.read_text():
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert pids and wait_dead(pids[0])
    assert not pid_alive(int(pid_file.read_text()))
