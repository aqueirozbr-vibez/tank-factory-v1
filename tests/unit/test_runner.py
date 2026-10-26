"""Unit 31: the task runner end to end with fakes, blocks, resume rules and the restart sweep."""

import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import pytest

from orchestrator.clock import utc_now
from orchestrator.github.client import IssueComment
from orchestrator.harness.fake import FakeHarness, FakeScript
from orchestrator.intake import Scheduler
from orchestrator.intake.resume import Resume
from orchestrator.models.enums import AgentRole, BlockedReason, EventKind, TaskStatus, WorkspaceKind
from orchestrator.models.settings import Settings
from orchestrator.phases.approval import PlanApprovals
from orchestrator.phases.plan import PlanPhase
from orchestrator.runner import LastBlock, RunnerDeps, TaskRunner, missing_holdout_ids
from orchestrator.supervisor import Supervisor
from orchestrator.trace import TraceWriter, verify_chain
from orchestrator.watchdogs import sweep_on_start
from orchestrator.workspace.git import run_git
from orchestrator.workspace.worktree import commit_all, create_worktree
from tests.unit.test_admission import FakeGitHub, unlabeled
from tests.unit.test_build_phase import CLAIM_WRONG, GREEN, PROTECTED, Recorder, make_config
from tests.unit.test_deliver import FakePulls, add_remote
from tests.unit.test_review_phase import ACCEPTED
from tests.unit.test_revision_loop import HUMAN, revised
from tests.unit.test_spec_gate import (
    HOLDOUT_IDS,
    SPEC_REL,
    SPEC_TEXT,
    TASK,
    FakeHoldout,
    issue,
    script,
)

ISSUE_BODY = "## Outcome\n\nDue date.\n\n## Acceptance criteria\n\n1. WHEN x THE SYSTEM SHALL y.\n"
PLAN = script(SPEC_TEXT)
QUESTIONS = script(SPEC_TEXT, open_questions=["Are terms calendar days?"], status="fail")


class FullFakeGitHub(FakeGitHub):
    """Issues, comments, labels and pull requests in one fake for the runner."""

    def __init__(self) -> None:
        super().__init__()
        self.issue_comments: list[IssueComment] = []
        self.pulls_api = FakePulls()

    async def get_issue(self, number: int, *, task_id: str) -> dict[str, Any]:
        return {
            "number": number,
            "title": "Invoice due date",
            "body": ISSUE_BODY,
            "user": {"login": "rsouza"},
            "author_association": "MEMBER",
            "labels": [],
        }

    async def list_issue_comments(self, number: int, *, task_id: str) -> list[IssueComment]:
        return list(self.issue_comments)

    def answer(self, body: str, *, at: str | None = None) -> None:
        self.issue_comments.append(
            IssueComment(
                id=len(self.issue_comments) + 1,
                author_login="rsouza",
                author_association="MEMBER",
                body=body,
                created_at=at or utc_now(),
            )
        )

    async def list_pulls_for_branch(self, head: str, *, task_id: str) -> list[dict[str, Any]]:
        return await self.pulls_api.list_pulls_for_branch(head, task_id=task_id)

    async def create_pull(
        self, *, title: str, head: str, base: str, body: str, task_id: str
    ) -> dict[str, Any]:
        self.calls.append(("create_pull", (title, head, base)))
        return await self.pulls_api.create_pull(
            title=title, head=head, base=base, body=body, task_id=task_id
        )

    async def request_reviewers(
        self, number: int, reviewers: Sequence[str], *, task_id: str, teams: Sequence[str] = ()
    ) -> None:
        self.calls.append(("request_reviewers", (number, tuple(reviewers), tuple(teams))))

    def labels_added(self) -> list[str]:
        return [
            label
            for kind, payload in self.calls
            if kind == "add_labels"
            for label in payload[1]  # type: ignore[index]
        ]


def labels_count(github: FullFakeGitHub, label: str) -> int:
    return github.labels_added().count(label)


class Stack:
    def __init__(
        self,
        trace_writer: TraceWriter,
        product: Path,
        tmp_path: Path,
        scripts: list[FakeScript],
        *,
        holdout_ids: set[str] | None = None,
        workspace: Literal["worktree", "container"] = "worktree",
        docker: object = None,
        task_image: str | None = None,
    ) -> None:
        self.writer = trace_writer
        self.product = product
        self.github = FullFakeGitHub()
        self.harness = FakeHarness(scripts)
        self.recorder = Recorder()
        self.holdout = FakeHoldout(set(HOLDOUT_IDS) if holdout_ids is None else holdout_ids)
        cfg = make_config()
        trace_writer.load_config_version(**cfg.config_version_row())
        settings = Settings(data_dir=tmp_path / "data", workspace=workspace, task_image=task_image)
        self.approvals = PlanApprovals(writer=trace_writer, github=self.github, repo_clone=product)
        deps = RunnerDeps(
            writer=trace_writer,
            config=cfg,
            settings=settings,
            harnesses={role: self.harness for role in AgentRole},
            github=self.github,
            repo_clone=product,
            holdout=self.holdout,
            approvals=self.approvals,
            base_branch="main",
            sleep=self.recorder.sleep,
            docker=docker,  # type: ignore[arg-type]
            task_image=task_image,
            harness_for=(
                (lambda _role, runner, _versions: self.harness.with_runner(runner))
                if docker is not None
                else None
            ),
        )
        self.runner = TaskRunner(deps)
        self.settings = settings
        self.config = cfg
        supervisor = Supervisor(lambda _tid: self._noop())
        self.scheduler = Scheduler(trace_writer, supervisor)
        self.resume = Resume(writer=trace_writer, github=self.github, scheduler=self.scheduler)

    async def _noop(self) -> None:
        return None

    def admit(self, *, status: TaskStatus = TaskStatus.RUNNING) -> None:
        self.writer.insert_task(
            task_id=TASK,
            issue_number=418,
            title="Invoice due date",
            branch="agent/418",
            workspace_kind=WorkspaceKind.WORKTREE,
            budget_usd=6.0,
            admitted_by="rsouza",
            config_sha=self.config.config_sha,
            status=status,
        )

    def task(self) -> sqlite3.Row:
        row = self.writer.get_task(TASK)
        assert row is not None
        return row

    def phases(self, run_id: str) -> list[str]:
        return [str(r["name"]) for r in self.writer.phases_for_run(run_id)]


def prepare(product: Path, tmp_path: Path) -> None:
    """The planner must write the spec itself; delivery needs a remote."""
    run_git(["rm", "-q", SPEC_REL], cwd=product)
    run_git(["commit", "-q", "-m", "no spec yet"], cwd=product)
    add_remote(product, tmp_path)


async def test_green_path_from_plan_to_pr(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    prepare(product, tmp_path)
    stack = Stack(trace_writer, product, tmp_path, [PLAN, GREEN, ACCEPTED])
    stack.admit()
    report = await stack.runner.run(TASK)
    assert report.delivered and report.entered_at == "plan"
    assert stack.phases(report.run_id) == [
        "plan",
        "spec_gate",
        "risk",
        "commit",
        "build",
        "code_gates",
        "commit",
        "review",
        "full_suite",
        "deliver",
        "ci_wait",
    ]
    task = stack.task()
    assert task["status"] == "pr_open" and task["pr_number"] == 431 and task["risk"] == "risk:low"
    assert stack.github.labels_added() == ["risk:low", "factory:review"]
    assert stack.github.comments() == []
    run = trace_writer.get_run(report.run_id)
    assert run is not None and run["status"] == "success"
    assert verify_chain(trace_writer.db).ok

    again = await stack.runner.run(TASK)  # a second start reconciles: no second PR
    assert again.delivered and again.entered_at == "deliver"
    assert len(stack.github.pulls_api.created()) == 1


async def test_repair_cap_block_posts_one_comment_and_resumes_per_rule(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    prepare(product, tmp_path)
    stack = Stack(trace_writer, product, tmp_path, [PLAN, CLAIM_WRONG])
    stack.admit()
    report = await stack.runner.run(TASK)
    assert report.blocked_reason is BlockedReason.REPAIR_CAP and report.notified
    comment = stack.github.comments()[-1]
    assert "**Blocked**: `repair_cap`" in comment and "in phase `code_gates`" in comment
    assert "artifacts_exist" in comment and "fresh caps" in comment
    assert labels_count(stack.github, "factory:needs-human") == 1
    assert stack.task()["status"] == "blocked"

    refused = await stack.resume.on_unlabeled(unlabeled(418, "factory:needs-human", actor="reader"))
    assert refused.kind == "refused_actor" and stack.task()["status"] == "blocked"
    assert "can resume a blocked task" in stack.github.comments()[-1]
    assert labels_count(stack.github, "factory:needs-human") == 2

    resumed = await stack.resume.on_unlabeled(unlabeled(418, "factory:needs-human"))
    assert (
        resumed.kind == "requeued"
        and resumed.rule == "new attempt on the same branch with fresh caps"
    )
    task = stack.task()
    assert task["attempt"] == 2 and task["blocked_reason"] is None and task["status"] == "running"
    control = trace_writer.db.fetchone(
        "SELECT actor, payload_json FROM events WHERE kind = 'control' ORDER BY seq DESC LIMIT 1"
    )
    assert (
        control is not None
        and control["actor"] == "rsouza"
        and '"resume"' in control["payload_json"]
    )


async def test_protected_path_and_budget_rules(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    prepare(product, tmp_path)
    stack = Stack(trace_writer, product, tmp_path, [PLAN, PROTECTED])
    stack.admit()
    report = await stack.runner.run(TASK)
    assert report.blocked_reason is BlockedReason.PROTECTED_PATH
    never = await stack.resume.on_unlabeled(unlabeled(418, "factory:needs-human"))
    assert never.kind == "not_resumable" and stack.task()["status"] == "blocked"
    assert "never resumes" in stack.github.comments()[-1]

    trace_writer.set_task_status(TASK, TaskStatus.BLOCKED, blocked_reason=BlockedReason.BUDGET)
    waiting = await stack.resume.on_unlabeled(unlabeled(418, "factory:needs-human"))
    assert waiting.kind == "budget_requires_raise" and "budget raise" in stack.github.comments()[-1]
    trace_writer.write_event(
        "control",  # type: ignore[arg-type]
        "mlima",
        task_id=TASK,
        payload={"action": "budget_raise", "factor": 1.5},
    )
    raised = await stack.resume.on_unlabeled(unlabeled(418, "factory:needs-human"))
    assert raised.kind == "requeued" and stack.task()["attempt"] == 2


async def test_open_questions_resume_only_after_an_answer(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    prepare(product, tmp_path)
    stack = Stack(trace_writer, product, tmp_path, [QUESTIONS, PLAN, GREEN, ACCEPTED])
    stack.admit()
    first = await stack.runner.run(TASK)
    assert first.blocked_reason is BlockedReason.OPEN_QUESTIONS
    assert len(stack.github.comments()) == 1 and "calendar days" in stack.github.comments()[0]
    assert labels_count(stack.github, "factory:needs-human") == 1

    await stack.resume.on_unlabeled(unlabeled(418, "factory:needs-human"))
    stayed = await stack.runner.run(TASK)
    assert stayed.blocked_reason is BlockedReason.OPEN_QUESTIONS and stayed.entered_at == "plan"
    assert len(stack.harness.calls) == 1  # no planner call without an answer
    assert "No answer was posted" in stack.github.comments()[-1]

    stack.github.answer("Calendar days.", at="2099-01-01T00:00:00.000+00:00")
    await stack.resume.on_unlabeled(unlabeled(418, "factory:needs-human", actor="mlima"))
    done = await stack.runner.run(TASK)
    assert done.delivered and stack.task()["status"] == "pr_open"
    assert len(stack.harness.calls) == 4


async def test_human_decision_needs_a_spec_edit(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    prepare(product, tmp_path)
    stack = Stack(trace_writer, product, tmp_path, [PLAN, GREEN, HUMAN, PLAN, revised(1), ACCEPTED])
    stack.admit()
    blocked = await stack.runner.run(TASK)
    assert blocked.blocked_reason is BlockedReason.HUMAN_DECISION_NEEDED
    assert len(stack.github.comments()) == 1  # the review loop's findings comment, nothing more

    await stack.resume.on_unlabeled(unlabeled(418, "factory:needs-human"))
    stayed = await stack.runner.run(TASK)
    assert stayed.blocked_reason is BlockedReason.HUMAN_DECISION_NEEDED
    assert "spec edit" in stack.github.comments()[-1] and len(stack.harness.calls) == 3

    wt = create_worktree(product, issue=418, worktrees_dir=stack.settings.worktrees_dir)
    spec = wt.path / "specs" / "418-invoice-due-date.md"
    spec.write_text(
        spec.read_text(encoding="utf-8") + "\nHuman note: terms are calendar days.\n",
        encoding="utf-8",
    )
    commit_all(wt.path, "spec: human decision")
    await stack.resume.on_unlabeled(unlabeled(418, "factory:needs-human"))
    done = await stack.runner.run(TASK)
    assert done.delivered and done.entered_at == "plan" and stack.task()["status"] == "pr_open"


async def test_missing_holdout_resume_checks_the_checkout(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    prepare(product, tmp_path)
    one_missing = {"tests/acceptance/test_invoice_due_date_holdout.py::test_due_date"}
    stack = Stack(
        trace_writer, product, tmp_path, [PLAN, PLAN, GREEN, ACCEPTED], holdout_ids=one_missing
    )
    stack.admit()
    blocked = await stack.runner.run(TASK)
    assert blocked.blocked_reason is BlockedReason.MISSING_HOLDOUT
    assert missing_holdout_ids(blocked.evidence) == [
        "tests/acceptance/test_invoice_due_date_holdout.py::test_zero_terms"
    ]
    await stack.resume.on_unlabeled(unlabeled(418, "factory:needs-human"))
    stayed = await stack.runner.run(TASK)
    assert stayed.blocked_reason is BlockedReason.MISSING_HOLDOUT and len(stack.harness.calls) == 1
    stack.holdout.ids.update(HOLDOUT_IDS)
    await stack.resume.on_unlabeled(unlabeled(418, "factory:needs-human"))
    done = await stack.runner.run(TASK)
    assert done.delivered and stack.holdout.refreshes >= 3


async def test_restart_sweep_mid_plan_replans_with_attempt_two(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    prepare(product, tmp_path)
    stack = Stack(trace_writer, product, tmp_path, [PLAN, GREEN, ACCEPTED])
    stack.admit()
    run_id = trace_writer.start_run(
        task_id=TASK, attempt=1, factory_version="t", config_sha=stack.config.config_sha
    )
    phase_id = trace_writer.start_phase(
        run_id=run_id,
        seq=1,
        name="plan",  # type: ignore[arg-type]
        kind="agent",  # type: ignore[arg-type]
        owner="planner",
        timeout_s=60,
    )
    process_id = trace_writer.start_process(
        run_id=run_id,
        phase_id=phase_id,
        kind="agent",  # type: ignore[arg-type]
        name="fake",
    )
    requeued = sweep_on_start(trace_writer)
    assert requeued == [TASK]
    task = stack.task()
    assert task["status"] == "queued" and task["attempt"] == 2
    phase = trace_writer.get_phase(phase_id)
    assert phase is not None and phase["status"] == "fail" and phase["error"] == "restart"
    process = trace_writer.get_process(process_id)
    assert process is not None and process["ended_at"] is not None
    assert await stack.scheduler.dispatch() == [TASK]

    report = await stack.runner.run(TASK)
    assert report.delivered and report.entered_at == "plan"
    run = trace_writer.get_run(report.run_id)
    assert run is not None and run["attempt"] == 2
    assert stack.github.comments() == []  # no duplicate comment on restart


async def test_identical_spec_rewrite_passes_the_claim_gates(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    prepare(product, tmp_path)
    stack = Stack(trace_writer, product, tmp_path, [PLAN, GREEN, ACCEPTED, PLAN])
    stack.admit()
    first = await stack.runner.run(TASK)
    assert first.delivered
    wt = create_worktree(product, issue=418, worktrees_dir=stack.settings.worktrees_dir)
    run_id = trace_writer.start_run(
        task_id=TASK, attempt=2, factory_version="t", config_sha=stack.config.config_sha
    )
    replan = await PlanPhase(
        stack.runner.deps.build_deps(AgentRole.PLANNER),
        task_id=TASK,
        worktree=wt,
        issue=issue(),
        run_id=run_id,
        holdout=stack.holdout,
        github=stack.github,
        instrument=stack.runner.instrument,
    ).run()
    assert replan.written and replan.corrections == 0
    logged = trace_writer.db.fetchone(
        "SELECT payload_json FROM events WHERE payload_json LIKE '%spec unchanged%'"
    )
    assert logged is not None


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        ("holdout tests missing: a::t1, b::t2", ["a::t1", "b::t2"]),
        ("something else", []),
    ],
)
def test_missing_holdout_ids(evidence: str, expected: list[str]) -> None:
    assert missing_holdout_ids(evidence) == expected


@pytest.mark.parametrize(
    ("reason", "point"),
    [
        (BlockedReason.REPAIR_CAP, "review"),
        (BlockedReason.FULL_SUITE, "review"),
        (BlockedReason.TIMEOUT, "review"),
        (BlockedReason.REVISION_CAP, "review"),
    ],
)
def test_a_committed_build_resumes_at_review_not_a_second_build(
    product: Path, tmp_path: Path, trace_writer: TraceWriter, reason: BlockedReason, point: str
) -> None:
    """TRD §7.8: repair_cap | full_suite | timeout resume on the same branch head. Rebuilding a
    committed build made the builder report that nothing changed, which blocked again."""
    stack = Stack(trace_writer, product, tmp_path, [PLAN])  # no harness call is made
    stack.admit(status=TaskStatus.BLOCKED)
    trace_writer.set_task_spec(TASK, spec_path=SPEC_REL, spec_sha="spec-sha")
    head = "c0ffee" * 6 + "abcd"
    trace_writer.write_event(
        EventKind.HANDOFF, "orchestrator", task_id=TASK, payload={"commit": head, "message": "m"}
    )
    previous = LastBlock(reason, "evidence", utc_now())
    assert stack.runner.resume_point(stack.task(), previous, head_sha=head) == point
    # a head that moved past the committed build is built again
    assert stack.runner.resume_point(stack.task(), previous, head_sha="f" * 40) in (
        "approval",
        "build",
    )
