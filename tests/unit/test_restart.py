"""Unit 45: restarts reconcile with GitHub before acting; five crashes, one PR, no duplicate."""

import asyncio
import contextlib
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from orchestrator.config import load_factory_config
from orchestrator.errors import HarnessError
from orchestrator.harness.process import ProcessOutcome
from orchestrator.harness.registry import make_harness, read_harness_versions
from orchestrator.models.enums import RiskClass
from orchestrator.models.settings import Settings
from orchestrator.phases.approval import PlanApprovals
from orchestrator.trace import TraceWriter
from orchestrator.watchdogs import sweep_on_start
from tests.unit.test_admission import FakeGitHub
from tests.unit.test_approval import approval_event, seed, start_waiter
from tests.unit.test_build_phase import GREEN
from tests.unit.test_review_phase import ACCEPTED
from tests.unit.test_runner import PLAN, Stack, prepare
from tests.unit.test_spec_gate import TASK

ROOT = Path(__file__).resolve().parents[2]


def arm_phase_crash(writer: TraceWriter, name: str) -> None:
    """The process dies the moment the named phase would start (a row never written)."""
    original = writer.start_phase

    def crashing(**kwargs: Any) -> str:  # noqa: ANN401 - forwards the writer's keywords
        if str(kwargs["name"]) == name:
            writer.start_phase = original  # type: ignore[method-assign]
            raise asyncio.CancelledError(f"crash at {name}")
        return original(**kwargs)

    writer.start_phase = crashing  # type: ignore[method-assign]


def entered_points(writer: TraceWriter) -> list[str]:
    rows = writer.db.fetchall(
        "SELECT payload_json FROM events WHERE kind = 'log'"
        ' AND payload_json LIKE \'%"runner":"start"%\' ORDER BY seq'
    )
    return [str(json.loads(r["payload_json"])["entered_at"]) for r in rows]


async def test_five_restarts_open_one_pr_and_no_duplicate_comments(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    prepare(product, tmp_path)
    stack = Stack(trace_writer, product, tmp_path, [PLAN, GREEN, ACCEPTED])
    stack.admit()
    github = stack.github
    original_create = github.create_pull
    crash_after_pr = {"armed": False}

    async def create_pull(**kwargs: Any) -> dict[str, Any]:  # noqa: ANN401 - forwards
        pull = await original_create(**kwargs)
        if crash_after_pr["armed"]:  # GitHub has the PR; the orchestrator never wrote pr_number
            crash_after_pr["armed"] = False
            raise asyncio.CancelledError("crash after create_pull")
        return pull

    github.create_pull = create_pull  # type: ignore[method-assign]

    for phase in ("build", "review", "deliver"):
        arm_phase_crash(trace_writer, phase)
        with pytest.raises(asyncio.CancelledError):
            await stack.runner.run(TASK)
        assert sweep_on_start(trace_writer) == [TASK]
        assert await stack.scheduler.dispatch() == [TASK]

    crash_after_pr["armed"] = True
    with pytest.raises(asyncio.CancelledError):
        await stack.runner.run(TASK)
    assert len(github.pulls_api.pulls) == 1
    assert sweep_on_start(trace_writer) == [TASK]
    assert await stack.scheduler.dispatch() == [TASK]

    arm_phase_crash(trace_writer, "ci_wait")
    with pytest.raises(asyncio.CancelledError):
        await stack.runner.run(TASK)
    assert sweep_on_start(trace_writer) == []  # the PR is open: nothing to requeue

    report = await stack.runner.run(TASK)
    assert report.delivered and report.entered_at == "deliver"
    assert len(github.pulls_api.pulls) == 1 and github.comments() == []
    assert len(stack.harness.calls) == 3  # planner, builder and reviewer ran once each
    task = stack.task()
    assert task["status"] == "pr_open" and task["pr_number"] == 431
    attempts = [
        int(r["attempt"])
        for r in trace_writer.db.fetchall("SELECT attempt FROM runs ORDER BY started_at, run_id")
    ]
    assert attempts == [1, 2, 3, 4, 5, 6]
    assert entered_points(trace_writer) == [
        "plan",
        "build",
        "review",
        "deliver",
        "deliver",
        "deliver",
    ]
    adopted = trace_writer.db.fetchall(
        "SELECT payload_json FROM events WHERE kind = 'log' AND payload_json LIKE '%pr_adopted%'"
    )
    assert len(adopted) == 1 and json.loads(adopted[0]["payload_json"])["pr_adopted"] == 431
    reconciled = trace_writer.db.fetchall(
        "SELECT payload_json FROM events WHERE kind = 'log'"
        ' AND payload_json LIKE \'%"reconcile":"github"%\''
    )
    assert len(reconciled) == 6
    ci_wait = trace_writer.db.fetchall("SELECT status FROM phases WHERE name = 'ci_wait'")
    assert [r["status"] for r in ci_wait] == ["running"]  # opened once, by the last run


async def test_plan_review_comment_is_not_reposted_after_a_restart(
    tmp_path: Path, trace_writer: TraceWriter
) -> None:
    github = FakeGitHub()
    run_id = seed(trace_writer, RiskClass.HIGH)
    approvals = PlanApprovals(writer=trace_writer, github=github, repo_clone=tmp_path)
    first = await start_waiter(trace_writer, github, approvals, RiskClass.HIGH, run_id)
    assert len(github.comments()) == 1
    first.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await first
    second = await start_waiter(
        trace_writer, github, approvals, RiskClass.HIGH, run_id, seq_start=5
    )
    assert len(github.comments()) == 1  # announced once; the label write repeats harmlessly
    labels = [c for c in github.calls if c[0] == "add_labels"]
    assert len(labels) == 2
    await approvals.on_label(approval_event("rsouza"))
    outcome = await second
    assert outcome.status == "approved" and outcome.waited


class VersionRunner:
    def __init__(self, answers: Mapping[str, str], *, missing: str | None = None) -> None:
        self.answers = answers
        self.missing = missing
        self.calls: list[list[str]] = []

    async def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_s: float,
        on_stdout_line: Callable[[str], None] | None = None,
        on_process: Callable[[int], None] | None = None,
        stdin_text: str | None = None,
    ) -> ProcessOutcome:
        if argv[0] == self.missing:
            raise FileNotFoundError(argv[0])
        self.calls.append(list(argv))
        if on_stdout_line is not None:
            on_stdout_line(self.answers[argv[0]])
        return ProcessOutcome(exit_code=0, timed_out=False, stderr_tail="", duration_s=0.1)


async def test_harness_versions_are_read_through_the_task_runner(tmp_path: Path) -> None:
    runner = VersionRunner({"claude": "2.1.309 (Claude Code)", "codex": "codex-cli 0.112.0"})
    versions = await read_harness_versions(
        runner, binaries={"claude_code": "claude", "codex": "codex"}, cwd=tmp_path
    )
    assert versions == {"claude_code": "2.1.309", "codex": "0.112.0"}
    assert runner.calls == [["claude", "--version"], ["codex", "--version"]]
    with pytest.raises(HarnessError):
        await read_harness_versions(
            VersionRunner({}, missing="claude"), binaries={"claude_code": "claude"}, cwd=tmp_path
        )
    cfg = load_factory_config(ROOT / "fixtures" / "factory_config")
    builder = make_harness(cfg.roster.builder, Settings(), runner=runner, version="2.1.309")
    assert builder.name == "claude_code" and builder.version() == "2.1.309"
    reviewer = make_harness(cfg.roster.reviewer, Settings(), version="0.112.0")
    assert reviewer.name == "codex" and reviewer.version() == "0.112.0"
