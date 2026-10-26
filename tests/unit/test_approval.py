"""Unit 25: plan approval bound to the spec sha; CODEOWNER approval for governed tasks."""

import asyncio
from pathlib import Path

from orchestrator import constants
from orchestrator.github.codeowners import is_owner, load_codeowners
from orchestrator.instruments.risk import RiskBreakdown, RiskInstrument, classify
from orchestrator.intake import Handlers, Router
from orchestrator.intake.issue_fetcher import TaggedIssue, parse_sections
from orchestrator.models.enums import ApprovalGate, RiskClass, TaskStatus, WorkspaceKind
from orchestrator.models.envelopes import RiskInputs
from orchestrator.models.events import NormalisedEvent
from orchestrator.phases.approval import (
    ApprovalOutcome,
    PlanApprovalPhase,
    PlanApprovals,
    gate_for,
)
from orchestrator.trace import TraceWriter, verify_chain
from orchestrator.workspace.git import run_git
from tests.unit.test_admission import FakeGitHub
from tests.unit.test_build_phase import make_config
from tests.unit.test_risk import INVOICE, RISK_YAML
from tests.unit.test_spec_gate import SPEC_REL, SPEC_TEXT, TASK

SHA_A = "a" * 40
SHA_B = "b" * 40
LATER = "2099-01-01T00:00:00.000+00:00"
EARLIER = "2000-01-01T00:00:00.000+00:00"
PLAN_APPROVED = constants.LABEL_PLAN_APPROVED
PLAN_REVIEW = constants.LABEL_PLAN_REVIEW


def approval_event(
    actor: str,
    *,
    at: str = LATER,
    delivery: str = "d1",
    issue: int = 418,
    source: str = "webhook",
) -> NormalisedEvent:
    poll = source == "poll"
    return NormalisedEvent(
        kind="issue_labeled",
        source="poll" if poll else "webhook",
        github_event_id=delivery,
        event_type="issues",
        action="labeled",
        issue_number=issue,
        actor_login=actor,
        label=PLAN_APPROVED,
        received_at=LATER if poll else at,
        occurred_at=at if poll else None,
    )


def issue() -> TaggedIssue:
    body = "## Outcome\n\nDue date.\n"
    return TaggedIssue(
        418, "Invoice due date", "rsouza", "MEMBER", True, body, parse_sections(body), ()
    )


def breakdown(risk: RiskClass) -> RiskBreakdown:
    instrument = RiskInstrument.from_yaml(RISK_YAML)
    if risk is RiskClass.GOVERNED:
        inputs = RiskInputs(tiers_touched=["A"], est_lines=20, modules=["billing"])
    elif risk is RiskClass.HIGH:
        inputs = RiskInputs(tiers_touched=["B"], est_lines=300, modules=["a", "b", "c"])
    else:
        inputs = RiskInputs(tiers_touched=["C"], est_lines=60, modules=["billing"])
    result = classify(instrument, scope=(INVOICE,), inputs=inputs, worktree=None)
    assert result.risk_class is risk
    return result


def seed(writer: TraceWriter, risk: RiskClass, *, spec_sha: str = SHA_A) -> str:
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
    writer.set_task_spec(TASK, spec_path=SPEC_REL, spec_sha=spec_sha)
    risk_breakdown = breakdown(risk)
    writer.set_task_risk(
        TASK,
        risk=str(risk),
        score=risk_breakdown.score,
        governed_path=risk_breakdown.governed_path,
    )
    return writer.start_run(task_id=TASK, attempt=1, factory_version="t", config_sha=cfg.config_sha)


async def start_waiter(
    writer: TraceWriter,
    github: FakeGitHub,
    approvals: PlanApprovals,
    risk: RiskClass,
    run_id: str,
    *,
    spec_sha: str = SHA_A,
    seq_start: int = 0,
) -> asyncio.Task[ApprovalOutcome]:
    phase = PlanApprovalPhase(
        writer=writer,
        github=github,
        approvals=approvals,
        task_id=TASK,
        issue=issue(),
        run_id=run_id,
        risk=breakdown(risk),
        spec_rel=SPEC_REL,
        spec_sha=spec_sha,
        spec_text=SPEC_TEXT,
        seq_start=seq_start,
    )
    task = asyncio.create_task(phase.run())
    for _ in range(20):
        await asyncio.sleep(0)
        if approvals.waiting(TASK) or task.done():
            break
    if task.done():
        task.result()  # surface a failure here rather than at the later await
    return task


def approvals_rows(writer: TraceWriter) -> list[dict[str, object]]:
    rows = writer.db.fetchall("SELECT * FROM approvals ORDER BY created_at, rowid")
    return [dict(row) for row in rows]


# --- helpers ---------------------------------------------------------------------------


def test_gate_for_and_is_owner() -> None:
    assert gate_for(RiskClass.HIGH) is ApprovalGate.PLAN
    assert gate_for("risk:governed") is ApprovalGate.GOVERNED
    assert gate_for(RiskClass.LOW) is None and gate_for(None) is None
    assert is_owner("MLima", ("@mlima",)) and is_owner("mlima", ("@rsouza", "@mlima"))
    assert not is_owner("mlima", ("@acme/dba",))  # teams are not resolved in v1
    assert not is_owner("mlima", ("mlima@example.test",)) and not is_owner(None, ("@mlima",))


def test_load_codeowners_from_the_default_branch(product: Path) -> None:
    assert load_codeowners(product) == []
    (product / ".github").mkdir()
    (product / ".github" / "CODEOWNERS").write_text("src/billing/ @mlima\n", encoding="utf-8")
    run_git(["add", "-A"], cwd=product)
    run_git(["commit", "-q", "-m", "owners"], cwd=product)
    rules = load_codeowners(product)
    assert [(r.pattern, r.owners) for r in rules] == [("src/billing/", ("@mlima",))]
    (product / "CODEOWNERS").write_text("* @rsouza\n", encoding="utf-8")
    run_git(["add", "-A"], cwd=product)
    run_git(["commit", "-q", "-m", "root owners"], cwd=product)
    assert [r.owners for r in load_codeowners(product)] == [("@rsouza",)]  # root wins


# --- the waiter and the service ----------------------------------------------------------


async def test_high_risk_waits_for_a_write_actor_on_the_current_sha(
    trace_writer: TraceWriter, product: Path
) -> None:
    run_id = seed(trace_writer, RiskClass.HIGH)
    github = FakeGitHub()
    approvals = PlanApprovals(writer=trace_writer, github=github, repo_clone=product)
    waiter = await start_waiter(trace_writer, github, approvals, RiskClass.HIGH, run_id)
    assert approvals.waiting(TASK) and not waiter.done()
    review = github.comments()[-1]
    assert "Plan review" in review and "109.2" in review and SPEC_REL in review
    assert "WHEN an Invoice is created" in review and PLAN_APPROVED in review
    assert ("add_labels", (418, (PLAN_REVIEW,))) in github.calls

    refused = await approvals.on_label(approval_event("reader"))
    assert refused.kind == "ignored_actor" and refused.permission == "read"
    assert "needs write access" in github.comments()[-1]
    assert ("remove_label", (418, PLAN_APPROVED)) in github.calls
    assert approvals_rows(trace_writer) == [] and not waiter.done()

    counted = await approvals.on_label(approval_event("rsouza", delivery="d2"))
    assert counted.kind == "approved" and counted.bound_sha == SHA_A
    outcome = await waiter
    assert outcome.approved and outcome.waited and outcome.actor == "rsouza"
    assert outcome.gate is ApprovalGate.PLAN and outcome.approval_id == counted.approval_id

    (row,) = approvals_rows(trace_writer)
    phase = trace_writer.db.fetchone(
        "SELECT phase_id, status FROM phases WHERE name = 'plan_approval'"
    )
    assert phase is not None and phase["status"] == "pass"
    assert row["gate"] == "plan" and row["actor_login"] == "rsouza"
    assert row["bound_sha"] == SHA_A and row["github_event_id"] == "d2"
    assert row["phase_id"] == phase["phase_id"] and row["codeowner"] is None
    assert github.calls[-1] == ("remove_label", (418, PLAN_REVIEW))
    approval_events = trace_writer.db.fetchall(
        "SELECT actor FROM events WHERE kind = 'approval' AND task_id = ?", (TASK,)
    )
    assert [e["actor"] for e in approval_events] == ["rsouza"]
    assert not approvals.waiting(TASK) and verify_chain(trace_writer.db).ok

    again = await approvals.on_label(approval_event("mlima", delivery="d3"))
    assert again.kind == "duplicate" and len(approvals_rows(trace_writer)) == 1


async def test_a_label_before_the_spec_commit_is_stale(
    trace_writer: TraceWriter, product: Path
) -> None:
    seed(trace_writer, RiskClass.HIGH)
    github = FakeGitHub()
    approvals = PlanApprovals(writer=trace_writer, github=github, repo_clone=product)
    webhook = await approvals.on_label(approval_event("rsouza", at=EARLIER))
    polled = await approvals.on_label(approval_event("rsouza", at=EARLIER, source="poll"))
    assert webhook.kind == "ignored_stale" and polled.kind == "ignored_stale"
    assert "before the current spec was committed" in github.comments()[-1]
    assert github.calls.count(("remove_label", (418, PLAN_APPROVED))) == 2
    assert approvals_rows(trace_writer) == []
    fresh = await approvals.on_label(approval_event("rsouza", at=LATER, source="poll"))
    assert fresh.kind == "approved"


async def test_governed_counts_only_a_codeowner_of_the_path(
    trace_writer: TraceWriter, product: Path
) -> None:
    (product / "CODEOWNERS").write_text("src/billing/ @mlima\ndocs/ @acme/docs\n", encoding="utf-8")
    run_git(["add", "-A"], cwd=product)
    run_git(["commit", "-q", "-m", "owners"], cwd=product)
    run_id = seed(trace_writer, RiskClass.GOVERNED)
    task = trace_writer.get_task(TASK)
    assert task is not None and task["governed_path"] == INVOICE
    github = FakeGitHub()
    approvals = PlanApprovals(writer=trace_writer, github=github, repo_clone=product)
    waiter = await start_waiter(trace_writer, github, approvals, RiskClass.GOVERNED, run_id)
    review = github.comments()[-1]
    assert "CODEOWNER" in review and "@mlima" in review and INVOICE in review

    outsider = await approvals.on_label(approval_event("rsouza"))
    assert outsider.kind == "ignored_not_owner" and outsider.owners == ("@mlima",)
    assert "@mlima" in github.comments()[-1] and not waiter.done()
    owner = await approvals.on_label(approval_event("mlima", delivery="d2"))
    assert owner.kind == "approved" and owner.gate is ApprovalGate.GOVERNED
    outcome = await waiter
    assert outcome.approved and outcome.actor == "mlima"
    (row,) = approvals_rows(trace_writer)
    assert row["gate"] == "governed" and row["codeowner"] == 1
    assert row["governed_path"] == INVOICE and row["actor_login"] == "mlima"


async def test_spec_edit_needs_a_new_approval_and_a_restart_reuses_one(
    trace_writer: TraceWriter, product: Path
) -> None:
    run_id = seed(trace_writer, RiskClass.HIGH)
    github = FakeGitHub()
    approvals = PlanApprovals(writer=trace_writer, github=github, repo_clone=product)
    first = await approvals.on_label(approval_event("rsouza"))  # no live waiter
    assert first.kind == "approved" and github.calls == []
    restarted = await start_waiter(trace_writer, github, approvals, RiskClass.HIGH, run_id)
    outcome = await restarted
    assert outcome.approved and not outcome.waited and outcome.actor == "rsouza"
    assert github.calls == []  # no review comment, no labels: the row already covered the sha

    trace_writer.set_task_spec(TASK, spec_path=SPEC_REL, spec_sha=SHA_B)  # the spec changed
    assert approvals.covering(TASK, gate=ApprovalGate.PLAN, bound_sha=SHA_B) is None
    assert approvals.covering(TASK, gate=ApprovalGate.PLAN, bound_sha=SHA_A) is not None
    waiter = await start_waiter(
        trace_writer, github, approvals, RiskClass.HIGH, run_id, spec_sha=SHA_B, seq_start=1
    )
    assert approvals.waiting(TASK) and not waiter.done()
    second = await approvals.on_label(approval_event("rsouza", delivery="d2"))
    assert second.kind == "approved" and second.bound_sha == SHA_B
    assert (await waiter).approved
    assert [r["bound_sha"] for r in approvals_rows(trace_writer)] == [SHA_A, SHA_B]


async def test_low_risk_and_unknown_issues_are_not_awaiting(
    trace_writer: TraceWriter, product: Path
) -> None:
    run_id = seed(trace_writer, RiskClass.LOW)
    github = FakeGitHub()
    approvals = PlanApprovals(writer=trace_writer, github=github, repo_clone=product)
    low = await approvals.on_label(approval_event("rsouza"))
    unknown = await approvals.on_label(approval_event("rsouza", issue=999, delivery="d9"))
    assert low.kind == unknown.kind == "ignored_not_awaiting"
    assert github.calls == [] and approvals_rows(trace_writer) == []
    skipped = await start_waiter(trace_writer, github, approvals, RiskClass.LOW, run_id)
    outcome = await skipped
    assert outcome.status == "not_required" and outcome.gate is None
    assert trace_writer.db.fetchall("SELECT 1 FROM phases WHERE name = 'plan_approval'") == []


async def test_router_delivers_the_label_to_the_service(
    trace_writer: TraceWriter, product: Path
) -> None:
    seed(trace_writer, RiskClass.HIGH)
    github = FakeGitHub()
    approvals = PlanApprovals(writer=trace_writer, github=github, repo_clone=product)

    async def never(event: NormalisedEvent) -> None:
        raise AssertionError("not routed here")

    router = Router(
        trace_writer, Handlers(admit=never, cancel=never, plan_approved=approvals.on_label)
    )
    assert await router.handle(approval_event("rsouza")) == "plan_approved"
    assert [r["actor_login"] for r in approvals_rows(trace_writer)] == ["rsouza"]
