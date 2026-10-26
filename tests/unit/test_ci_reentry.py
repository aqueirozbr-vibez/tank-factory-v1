"""Unit 34: CI failures and change requests re-enter the build on the same PR."""

import json
from pathlib import Path

from orchestrator import constants
from orchestrator.intake import Handlers, Router
from orchestrator.intake.ci import CiReentry, pending_reentry
from orchestrator.intake.webhook import normalise
from orchestrator.models.enums import BlockedReason, PhaseName
from orchestrator.models.events import NormalisedEvent
from orchestrator.phases.deliver import MergeObserver
from orchestrator.trace import TraceWriter, verify_chain
from orchestrator.workspace.git import run_git
from tests.unit.test_admission import unlabeled
from tests.unit.test_build_phase import GREEN
from tests.unit.test_deliver import merged_event
from tests.unit.test_review_phase import ACCEPTED
from tests.unit.test_revision_loop import revised
from tests.unit.test_runner import PLAN, Stack, prepare
from tests.unit.test_spec_gate import TASK

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "webhooks"
AT = "2026-10-24T12:00:00.000+00:00"


def ci_event(
    sha: str, *, conclusion: str = "failure", delivery: str = "ci-1", pr: int = 431
) -> NormalisedEvent:
    return NormalisedEvent(
        kind="check_run_completed",
        source="webhook",
        github_event_id=delivery,
        event_type="check_run",
        action="completed",
        pr_number=pr,
        branch="agent/418",
        head_sha=sha,
        conclusion=conclusion,
        received_at=AT,
    )


def review_event(state: str = "changes_requested", *, delivery: str = "rv-1") -> NormalisedEvent:
    return NormalisedEvent(
        kind="review_submitted",
        source="webhook",
        github_event_id=delivery,
        event_type="pull_request_review",
        action="submitted",
        pr_number=431,
        branch="agent/418",
        review_state=state,
        actor_login="mlima",
        received_at=AT,
    )


async def delivered_stack(
    trace_writer: TraceWriter, product: Path, tmp_path: Path, extra: list[object]
) -> Stack:
    prepare(product, tmp_path)
    stack = Stack(trace_writer, product, tmp_path, [PLAN, GREEN, ACCEPTED, *extra])  # type: ignore[list-item]
    stack.admit()
    first = await stack.runner.run(TASK)
    assert first.delivered
    return stack


async def test_failing_check_run_reenters_once_and_keeps_the_pr(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    stack = await delivered_stack(trace_writer, product, tmp_path, [revised(1), ACCEPTED])
    task = stack.task()
    sha = str(task["pr_head_sha"])
    waiting = trace_writer.open_phase(TASK, PhaseName.CI_WAIT)
    assert waiting is not None and waiting["owner"] == "github" and waiting["kind"] == "engineer"

    ci = CiReentry(writer=trace_writer, scheduler=stack.scheduler)
    assert (await ci.on_ci_failed(ci_event("0" * 40))).kind == "ignored_stale"
    assert (await ci.on_ci_failed(ci_event(sha, conclusion="success"))).kind == "ignored"
    result = await ci.on_ci_failed(ci_event(sha))
    assert result.kind == "requeued" and result.cause == "ci_failure"
    assert (await ci.on_ci_failed(ci_event(sha, delivery="ci-2"))).kind == "ignored_not_pr_open"
    failed = trace_writer.get_phase(waiting["phase_id"])
    assert failed is not None and failed["status"] == "fail" and failed["error"] == "ci_failure"
    assert pending_reentry(trace_writer, TASK) is not None
    assert stack.task()["status"] == "running" and stack.task()["attempt"] == 2

    report = await stack.runner.run(TASK)
    assert report.delivered and report.entered_at == "build"
    after = stack.task()
    assert (
        after["pr_number"] == 431 and after["status"] == "pr_open" and after["pr_head_sha"] != sha
    )
    builder_request = stack.harness.calls[3]
    assert "## Previous verification result" in builder_request.prompt
    assert "ci_failure" in builder_request.prompt and '"gate": "ci"' in builder_request.prompt
    assert len(stack.github.pulls_api.created()) == 1
    names = stack.phases(report.run_id)
    assert names[0] == "build" and names[-2:] == ["deliver", "ci_wait"]
    assert pending_reentry(trace_writer, TASK) is None
    remote = run_git(["rev-parse", "refs/heads/agent/418"], cwd=tmp_path / "remote.git").stdout
    assert remote.strip() == after["pr_head_sha"]
    assert verify_chain(trace_writer.db).ok


async def test_changes_requested_through_the_router(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    stack = await delivered_stack(trace_writer, product, tmp_path, [revised(1), ACCEPTED])
    ci = CiReentry(writer=trace_writer, scheduler=stack.scheduler)

    async def never(event: NormalisedEvent) -> None:
        raise AssertionError("not routed here")

    router = Router(
        trace_writer,
        Handlers(
            admit=never, cancel=never, review=ci.on_changes_requested, ci_failed=ci.on_ci_failed
        ),
    )
    assert await router.handle(review_event("approved", delivery="rv-0")) == "ignored"
    assert await router.handle(review_event()) == "review"
    control = trace_writer.latest_control(TASK, "ci_reentry")
    assert control is not None and "requested changes" in control["payload_json"]
    report = await stack.runner.run(TASK)
    assert report.delivered and report.entered_at == "build"
    assert "changes_requested" in stack.harness.calls[3].prompt

    payload = json.loads((FIXTURES / "check_run_failure.json").read_text(encoding="utf-8"))
    event = normalise("check_run", "d-fixture", payload)
    assert event.kind == "check_run_completed" and event.pr_number == 431
    assert await router.handle(event) == "ci_failed"  # a stale sha from the fixture: ignored
    assert stack.task()["status"] == "pr_open"


async def test_fourth_reentry_blocks_repair_cap_until_a_resume(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    stack = await delivered_stack(trace_writer, product, tmp_path, [revised(1), ACCEPTED])
    sha = str(stack.task()["pr_head_sha"])
    ci = CiReentry(writer=trace_writer, scheduler=stack.scheduler)

    def earlier(n: int) -> None:
        trace_writer.write_event(
            "control",  # type: ignore[arg-type]
            "github",
            task_id=TASK,
            payload={"action": "ci_reentry", "cause": "ci_failure", "detail": f"earlier {n}"},
        )

    earlier(0)
    assert (await ci.on_ci_failed(ci_event(sha))).kind == "ignored_pending"  # already pending
    for n in range(1, constants.REPAIR_CAP + 1):
        earlier(n)  # four re-entries since the last resume: one past the cap
    trace_writer.requeue_task(TASK)
    assert await stack.scheduler.dispatch() == [TASK]
    calls_before = len(stack.harness.calls)
    blocked = await stack.runner.run(TASK)
    assert blocked.blocked_reason is BlockedReason.REPAIR_CAP and blocked.entered_at == "build"
    assert len(stack.harness.calls) == calls_before  # no builder call past the cap
    assert (
        "repair_cap" in stack.github.comments()[-1] and "ci_failure" in stack.github.comments()[-1]
    )

    resumed = await stack.resume.on_unlabeled(unlabeled(418, "factory:needs-human"))
    assert resumed.kind == "requeued"
    report = await stack.runner.run(TASK)
    assert report.delivered and report.entered_at == "build"  # fresh caps after the resume
    assert stack.task()["pr_number"] == 431


async def test_merge_passes_the_ci_wait_row(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    stack = await delivered_stack(trace_writer, product, tmp_path, [])
    waiting = trace_writer.open_phase(TASK, PhaseName.CI_WAIT)
    assert waiting is not None
    assert await MergeObserver(trace_writer).on_merged(merged_event(431)) is True
    closed = trace_writer.get_phase(waiting["phase_id"])
    assert closed is not None and closed["status"] == "pass"
    assert stack.task()["status"] == "merged"
    assert trace_writer.open_phase(TASK, PhaseName.CI_WAIT) is None
