"""Unit 41: spend watchdog, budget block, one-time raise, daily hold and the dry run."""

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from orchestrator import constants
from orchestrator.budget.controls import raise_task_budget
from orchestrator.budget.dry_run import DryRunIssue, InstallLog, derive_budget
from orchestrator.budget.keys import PhaseKeys
from orchestrator.harness.fake import FakeHarness, FakeScript
from orchestrator.intake.scheduler import Scheduler
from orchestrator.issue_to_pr import run_build_slice
from orchestrator.models.enums import BlockedReason, TaskStatus, WorkspaceKind
from orchestrator.models.settings import Settings
from orchestrator.phases.build import BuildDeps
from orchestrator.supervisor import Supervisor
from orchestrator.trace import TraceWriter
from orchestrator.workspace.worktree import create_worktree
from tests.unit.test_budget_watchdogs import _other_task_spend
from tests.unit.test_build_phase import ENVELOPE, GREEN, SPEC_REL, TASK_ID, make_config
from tests.unit.test_proxy import FakeLiteLLM
from tests.unit.test_review_phase import ACCEPTED
from tests.unit.test_runner import PLAN, Stack, prepare
from tests.unit.test_spec_gate import TASK

SLEEPY = FakeScript(sleep_s=30, envelope=ENVELOPE)


def admitted(trace_writer: TraceWriter, *, budget_usd: float, spend: float) -> BuildDeps:
    """A task under a fake proxy whose every key reports ``spend``; the watchdog ticks fast."""
    cfg = make_config(builder_timeout_s=120)
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
    fake = FakeLiteLLM(default_spend=spend)
    return BuildDeps(
        writer=trace_writer,
        config=cfg,
        settings=Settings(),
        harness=FakeHarness([SLEEPY]),
        factory_version="0.1.0-test",
        keys=PhaseKeys(fake.admin(), watch_interval_s=0.1),
    )


def budget_stops(trace_writer: TraceWriter) -> list[dict[str, object]]:
    rows = trace_writer.db.fetchall(
        "SELECT payload_json FROM events WHERE kind = 'budget_stop' ORDER BY seq"
    )
    return [json.loads(r["payload_json"]) for r in rows]


async def test_watchdog_stops_a_phase_past_the_task_budget(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    deps = admitted(trace_writer, budget_usd=4.0, spend=4.5)  # the pre-check passes (0 + 3 ≤ 4)
    wt = create_worktree(product, issue=418, worktrees_dir=tmp_path / "w")
    started = time.monotonic()
    outcome = await run_build_slice(deps, task_id=TASK_ID, worktree=wt, spec_rel=SPEC_REL)
    elapsed = time.monotonic() - started
    assert elapsed < constants.KILL_SWITCH_BOUND_S, elapsed
    assert outcome.blocked_reason is BlockedReason.BUDGET and outcome.builder_calls == 1
    assert "error_max_budget_usd" in outcome.evidence and "task cap" in outcome.evidence
    stops = budget_stops(trace_writer)
    assert len(stops) == 1 and stops[0]["cap"] == "task"
    assert (stops[0]["task_budget_usd"], stops[0]["phase_spend_usd"]) == (4.0, 4.5)
    task = trace_writer.get_task(TASK_ID)
    assert task is not None and task["blocked_reason"] == "budget"
    assert task["spent_usd"] == pytest.approx(4.5)  # the proxy's figure is the phase cost
    phase = trace_writer.db.fetchone(
        "SELECT cost_usd, status, error FROM phases WHERE name = 'build'"
    )
    assert phase is not None and phase["cost_usd"] == 4.5 and phase["status"] == "fail"
    assert "error_max_budget_usd" in phase["error"]
    assert trace_writer.count_open_processes() == 0


async def test_watchdog_trips_on_the_daily_cap(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    deps = admitted(trace_writer, budget_usd=100.0, spend=4.0)
    _other_task_spend(trace_writer, 50.5)  # daily cap 54: 50.5 + 3 fits before, 50.5 + 4 trips
    wt = create_worktree(product, issue=418, worktrees_dir=tmp_path / "w")
    outcome = await run_build_slice(deps, task_id=TASK_ID, worktree=wt, spec_rel=SPEC_REL)
    assert outcome.blocked_reason is BlockedReason.BUDGET and "daily cap" in outcome.evidence
    stops = budget_stops(trace_writer)
    assert stops[-1]["cap"] == "daily" and stops[-1]["daily_cap_usd"] == 54.0
    assert stops[-1]["daily_spent_usd"] == pytest.approx(54.5)


async def test_budget_block_posts_the_spend_comment_and_the_label(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    prepare(product, tmp_path)
    stack = Stack(trace_writer, product, tmp_path, [PLAN, GREEN, ACCEPTED])
    stack.admit()
    trace_writer.set_task_budget(
        TASK, 1.0
    )  # below the planner's phase cap: blocked before the call
    report = await stack.runner.run(TASK)
    assert report.status == "blocked" and report.blocked_reason is BlockedReason.BUDGET
    labels = stack.github.labels_added()
    assert constants.LABEL_BUDGET in labels and constants.LABEL_NEEDS_HUMAN in labels
    comments = stack.github.comments()
    assert len(comments) == 1 and comments[0].startswith("**Budget stop**")
    assert "$0.00 of its $1.00 budget" in comments[0] and "factory budget --task" in comments[0]
    assert not stack.resume.budget_raised_since_block(TASK)
    raised = raise_task_budget(
        trace_writer, task_id=TASK, new_budget_usd=2.0, actor="rsouza", reason="one more go"
    )
    assert raised.ok and raised.http_status == 200
    assert stack.resume.budget_raised_since_block(TASK)
    again = raise_task_budget(
        trace_writer, task_id=TASK, new_budget_usd=3.0, actor="rsouza", reason="and again"
    )
    assert again.code == "already_raised" and again.http_status == 409


def test_raise_rules(trace_writer: TraceWriter) -> None:
    cfg = make_config()
    trace_writer.load_config_version(**cfg.config_version_row())
    trace_writer.insert_task(
        task_id=TASK_ID,
        issue_number=418,
        title="t",
        branch="agent/418",
        workspace_kind=WorkspaceKind.WORKTREE,
        budget_usd=6.0,
        admitted_by="rsouza",
        config_sha=cfg.config_sha,
    )
    missing = raise_task_budget(
        trace_writer, task_id="acme/atlas-api#999", new_budget_usd=9.0, actor="a", reason="r"
    )
    assert missing.code == "not_found" and missing.http_status == 404
    same = raise_task_budget(
        trace_writer, task_id=TASK_ID, new_budget_usd=6.0, actor="a", reason="r"
    )
    assert same.code == "not_an_increase" and same.http_status == 422
    over = raise_task_budget(
        trace_writer, task_id=TASK_ID, new_budget_usd=12.5, actor="a", reason="r"
    )
    assert over.code == "over_factor" and over.http_status == 422
    ok = raise_task_budget(
        trace_writer, task_id=TASK_ID, new_budget_usd=12.0, actor="rsouza", reason="long spec"
    )
    assert ok.ok and (ok.old_budget_usd, ok.new_budget_usd) == (6.0, 12.0)
    task = trace_writer.get_task(TASK_ID)
    assert task is not None and task["budget_usd"] == 12.0
    control = trace_writer.db.fetchone(
        "SELECT actor, payload_json FROM events WHERE kind = 'control' ORDER BY seq DESC LIMIT 1"
    )
    assert control is not None and control["actor"] == "rsouza"
    payload = json.loads(control["payload_json"])
    assert payload["action"] == "budget_raise" and payload["factor"] == 2.0
    assert payload["reason"] == "long spec" and ok.event_seq is not None
    second = raise_task_budget(
        trace_writer, task_id=TASK_ID, new_budget_usd=13.0, actor="a", reason="r"
    )
    assert second.code == "already_raised"


async def test_scheduler_holds_the_queue_until_the_day_rolls_over(
    trace_writer: TraceWriter,
) -> None:
    trace_writer.insert_task(
        task_id=TASK_ID,
        issue_number=418,
        title="t",
        branch="agent/418",
        workspace_kind=WorkspaceKind.WORKTREE,
        budget_usd=6.0,
        admitted_by="rsouza",
        config_sha="cfg1",
        status=TaskStatus.QUEUED,
    )
    _other_task_spend(trace_writer, 54.0)  # today's spend reaches the daily cap (3 x 3 x 6)
    moment = [datetime.now(UTC)]

    async def noop(_task_id: str) -> None:
        return None

    scheduler = Scheduler(trace_writer, Supervisor(noop), clock=lambda: moment[0])
    assert await scheduler.dispatch() == []
    assert await scheduler.dispatch() == []
    holds = trace_writer.db.fetchall(
        "SELECT payload_json FROM events"
        " WHERE kind = 'log' AND payload_json LIKE '%daily_cap_hold%'"
    )
    assert len(holds) == 1 and json.loads(holds[0]["payload_json"])["daily_cap_usd"] == 54.0
    moment[0] = moment[0] + timedelta(days=1)
    started = await scheduler.dispatch()
    assert sorted(started) == sorted(["acme/atlas-api#1", TASK_ID])


def test_derive_budget_and_install_log(tmp_path: Path) -> None:
    runs = (
        DryRunIssue(1, True, 0.32),
        DryRunIssue(2, True, 0.34),
        DryRunIssue(3, False, 0.10, "repair_cap"),
    )
    derived = derive_budget(runs)
    assert not derived.refused and derived.per_task_budget_usd == 5.0  # the industry floor
    assert "industry per-task cap" in derived.reason and derived.floor_usd == 5.0
    assert derived.completed == 2 and derived.highest_cost_usd == 0.34
    costly = derive_budget((DryRunIssue(1, True, 3.1), DryRunIssue(2, True, 2.0)))
    assert costly.per_task_budget_usd == 6.2 and "2x the highest" in costly.reason
    refused = derive_budget((runs[0], DryRunIssue(2, False, 0.0, "timeout"), runs[2]))
    assert refused.refused and "refuses to go live" in refused.reason
    zero = derive_budget((DryRunIssue(1, True, 0.0), DryRunIssue(2, True, 0.0)))
    assert zero.refused and "no cost" in zero.reason
    log = InstallLog(tmp_path / "data")
    log.append(derived.record())
    log.append(refused.record())
    records = log.records()
    assert [r["refused"] for r in records] == [False, True]
    assert records[0]["per_task_budget_usd"] == 5.0 and records[0]["floor_usd"] == 5.0
    assert "at" in records[0]
    assert records[0]["required"] == constants.DRY_RUN_MIN_COMPLETE
