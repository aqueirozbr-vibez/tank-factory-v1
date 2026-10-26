"""Plan approval: the GitHub label is the only approval record (PRD §Capability 2; TRD §6.2).

``PlanApprovals`` judges every ``factory:plan-approved`` label event, live
waiter or not, and records the counted ones in ``approvals`` bound to the
task's current spec sha. ``PlanApprovalPhase`` runs inside the task: it
posts the plan for review and waits for a counted verdict on its sha.
"""

import asyncio
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from orchestrator import constants
from orchestrator.clock import parse_ts
from orchestrator.github.codeowners import is_owner, load_codeowners, owners_for
from orchestrator.instruments.risk import RiskBreakdown
from orchestrator.intake import messages
from orchestrator.intake.actors import actor_permission, has_write
from orchestrator.intake.issue_fetcher import TaggedIssue
from orchestrator.intake.ports import GitHubPort
from orchestrator.models.enums import (
    ApprovalGate,
    EventKind,
    PhaseKind,
    PhaseName,
    PhaseStatus,
    RiskClass,
    TaskStatus,
)
from orchestrator.models.events import NormalisedEvent
from orchestrator.notify.slack import NotifierPort, plan_review_notice
from orchestrator.phases.recorder import PhaseRecorder
from orchestrator.trace.writer import TraceWriter

VerdictKind = Literal[
    "approved",
    "duplicate",
    "ignored_not_awaiting",
    "ignored_stale",
    "ignored_actor",
    "ignored_not_owner",
]
FINISHED = {TaskStatus.CANCELLED, TaskStatus.MERGED}
APPROVAL_PHASE_KIND = PhaseKind.CODE


def gate_for(risk: RiskClass | str | None) -> ApprovalGate | None:
    """Which approval a risk class needs before the build; ``None`` for ``risk:low``."""
    if risk is None:
        return None
    value = str(risk)
    if value == RiskClass.HIGH:
        return ApprovalGate.PLAN
    if value == RiskClass.GOVERNED:
        return ApprovalGate.GOVERNED
    return None


@dataclass(frozen=True)
class Verdict:
    kind: VerdictKind
    task_id: str | None
    actor: str | None
    github_event_id: str
    gate: ApprovalGate | None = None
    bound_sha: str | None = None
    approval_id: str | None = None
    permission: str | None = None
    governed_path: str | None = None
    owners: tuple[str, ...] = ()

    @property
    def counted(self) -> bool:
        return self.kind in ("approved", "duplicate")

    def payload(self) -> dict[str, object]:
        return {
            "verdict": self.kind,
            "actor": self.actor,
            "github_event_id": self.github_event_id,
            "gate": None if self.gate is None else str(self.gate),
            "bound_sha": self.bound_sha,
            "approval_id": self.approval_id,
            "permission": self.permission,
            "governed_path": self.governed_path,
            "owners": list(self.owners),
        }


class PlanApprovals:
    """The ``plan_approved`` handler and the registry of live waiters."""

    def __init__(
        self,
        *,
        writer: TraceWriter,
        github: GitHubPort,
        repo_clone: Path,
        ref: str | None = None,
        notifier: NotifierPort | None = None,
    ) -> None:
        self.writer = writer
        self.github = github
        self.repo_clone = repo_clone
        self.ref = ref
        self.notifier = notifier  # Slack: a notification, never an approval (unit 50)
        self._waiters: dict[str, tuple[str, asyncio.Queue[Verdict]]] = {}

    # --- waiters --------------------------------------------------------------------

    def register(self, task_id: str, *, phase_id: str) -> asyncio.Queue[Verdict]:
        queue: asyncio.Queue[Verdict] = asyncio.Queue()
        self._waiters[task_id] = (phase_id, queue)
        return queue

    def unregister(self, task_id: str) -> None:
        self._waiters.pop(task_id, None)

    def waiting(self, task_id: str) -> bool:
        return task_id in self._waiters

    def covering(self, task_id: str, *, gate: ApprovalGate, bound_sha: str) -> sqlite3.Row | None:
        return self.writer.approval_for(task_id, gate=gate, bound_sha=bound_sha)

    def owners_of(self, path: str) -> tuple[str, ...]:
        return owners_for(path, load_codeowners(self.repo_clone, ref=self.ref))

    # --- the label event --------------------------------------------------------------

    def _task_id(self, issue: int) -> str:
        installation = self.writer.get_installation()
        if installation is None:
            raise RuntimeError("the installation row is missing")
        return f"{installation['org']}/{installation['repo']}#{issue}"

    async def on_label(self, event: NormalisedEvent) -> Verdict:
        if event.issue_number is None:
            return Verdict("ignored_not_awaiting", None, event.actor_login, event.github_event_id)
        issue = event.issue_number
        task_id = self._task_id(issue)
        task = self.writer.get_task(task_id)
        gate = gate_for(task["risk"]) if task is not None else None
        spec_sha = str(task["spec_sha"] or "") if task is not None else ""
        if task is None or gate is None or not spec_sha or task["status"] in FINISHED:
            verdict = Verdict(
                "ignored_not_awaiting", task_id, event.actor_login, event.github_event_id
            )
            self.writer.write_event(
                EventKind.LOG,
                "orchestrator",
                task_id=task_id if task is not None else None,
                payload={"approval": verdict.payload(), "issue": issue},
            )
            return verdict

        occurred = event.occurred_at or event.received_at
        committed = task["spec_committed_at"]
        if committed and parse_ts(occurred) < parse_ts(str(committed)):
            return await self._ignore(
                Verdict(
                    "ignored_stale",
                    task_id,
                    event.actor_login,
                    event.github_event_id,
                    gate=gate,
                    bound_sha=spec_sha,
                ),
                issue,
            )

        permission = await actor_permission(
            self.writer, self.github, event.actor_login, task_id=task_id
        )
        if not has_write(permission):
            return await self._ignore(
                Verdict(
                    "ignored_actor",
                    task_id,
                    event.actor_login,
                    event.github_event_id,
                    gate=gate,
                    bound_sha=spec_sha,
                    permission=permission,
                ),
                issue,
            )

        governed_path: str | None = None
        owners: tuple[str, ...] = ()
        codeowner: bool | None = None
        if gate is ApprovalGate.GOVERNED:
            governed_path = str(task["governed_path"] or task["spec_path"])
            owners = self.owners_of(governed_path)
            codeowner = is_owner(event.actor_login, owners)
            if not codeowner:
                return await self._ignore(
                    Verdict(
                        "ignored_not_owner",
                        task_id,
                        event.actor_login,
                        event.github_event_id,
                        gate=gate,
                        bound_sha=spec_sha,
                        permission=permission,
                        governed_path=governed_path,
                        owners=owners,
                    ),
                    issue,
                )

        waiter = self._waiters.get(task_id)
        approval_id = self.writer.record_approval(
            task_id=task_id,
            gate=gate,
            actor_login=str(event.actor_login),
            bound_sha=spec_sha,
            github_event_id=event.github_event_id,
            phase_id=waiter[0] if waiter else None,
            governed_path=governed_path,
            codeowner=codeowner,
        )
        verdict = Verdict(
            "approved" if approval_id else "duplicate",
            task_id,
            event.actor_login,
            event.github_event_id,
            gate=gate,
            bound_sha=spec_sha,
            approval_id=approval_id,
            permission=permission,
            governed_path=governed_path,
            owners=owners,
        )
        self.writer.write_event(
            EventKind.APPROVAL,
            str(event.actor_login),
            task_id=task_id,
            phase_id=waiter[0] if waiter else None,
            payload={**verdict.payload(), "live_waiter": waiter is not None},
        )
        if waiter is not None:
            waiter[1].put_nowait(verdict)
        return verdict

    async def _ignore(self, verdict: Verdict, issue: int) -> Verdict:
        assert verdict.task_id is not None
        self.writer.write_event(
            EventKind.LOG,
            "orchestrator",
            task_id=verdict.task_id,
            payload={"approval": verdict.payload(), "issue": issue},
        )
        await self.github.create_comment(
            issue,
            messages.approval_ignored(
                verdict.actor,
                verdict.kind,
                permission=verdict.permission,
                governed_path=verdict.governed_path,
                owners=verdict.owners,
            ),
            task_id=verdict.task_id,
        )
        await self.github.remove_label(
            issue, constants.LABEL_PLAN_APPROVED, task_id=verdict.task_id
        )
        waiter = self._waiters.get(verdict.task_id)
        if waiter is not None:
            waiter[1].put_nowait(verdict)
        return verdict


@dataclass(frozen=True)
class ApprovalOutcome:
    status: Literal["approved", "not_required"]
    gate: ApprovalGate | None
    bound_sha: str
    actor: str | None = None
    approval_id: str | None = None
    waited: bool = False

    @property
    def approved(self) -> bool:
        return self.status == "approved"


class PlanApprovalPhase:
    def __init__(
        self,
        *,
        writer: TraceWriter,
        github: GitHubPort | None,
        approvals: PlanApprovals,
        task_id: str,
        issue: TaggedIssue,
        run_id: str,
        risk: RiskBreakdown,
        spec_rel: str,
        spec_sha: str,
        spec_text: str,
        attempt: int = 1,
        seq_start: int = 0,
    ) -> None:
        self.writer = writer
        self.github = github
        self.approvals = approvals
        self.task_id = task_id
        self.issue = issue
        self.risk = risk
        self.spec_rel = spec_rel
        self.spec_sha = spec_sha
        self.spec_text = spec_text
        self.attempt = attempt
        self.recorder = PhaseRecorder(writer, task_id=task_id, run_id=run_id, seq_start=seq_start)

    @property
    def next_seq(self) -> int:
        return self.recorder.next_seq

    async def run(self) -> ApprovalOutcome:
        gate = gate_for(self.risk.risk_class)
        if gate is None:
            return ApprovalOutcome("not_required", None, self.spec_sha)
        phase_id = self.recorder.start(
            PhaseName.PLAN_APPROVAL,
            APPROVAL_PHASE_KIND,
            "orchestrator",
            attempt=self.attempt,
            timeout_s=constants.PLAN_APPROVAL_TIMEOUT_S,
        )
        existing = self.approvals.covering(self.task_id, gate=gate, bound_sha=self.spec_sha)
        if existing is not None:
            self.recorder.event(
                EventKind.LOG,
                {
                    "approval": "already_covered",
                    "approval_id": existing["id"],
                    "actor": existing["actor_login"],
                    "bound_sha": self.spec_sha,
                },
                phase_id=phase_id,
            )
            self.recorder.finish(phase_id, PhaseStatus.PASS)
            return ApprovalOutcome(
                "approved",
                gate,
                self.spec_sha,
                actor=str(existing["actor_login"]),
                approval_id=str(existing["id"]),
                waited=False,
            )

        governed_path = self.risk.governed_path if gate is ApprovalGate.GOVERNED else None
        owners = self.approvals.owners_of(governed_path) if governed_path else ()
        queue = self.approvals.register(self.task_id, phase_id=phase_id)
        announced = self.writer.awaiting_logged(self.task_id, self.spec_sha)
        try:
            if self.github is not None and not announced:  # a restart does not repost
                await self.github.create_comment(
                    self.issue.number,
                    messages.plan_review(
                        risk_class=str(self.risk.risk_class),
                        equation=self.risk.equation(),
                        conclusion=self.risk.conclusion(),
                        spec_rel=self.spec_rel,
                        spec_text=self.spec_text,
                        governed_path=governed_path,
                        owners=owners,
                    ),
                    task_id=self.task_id,
                )
            if self.approvals.notifier is not None and not announced:
                await self.approvals.notifier.send(
                    plan_review_notice(
                        task_id=self.task_id,
                        issue_number=self.issue.number,
                        title=self.issue.title,
                        risk_class=str(self.risk.risk_class),
                        spec_rel=self.spec_rel,
                        governed_path=governed_path,
                        owners=owners,
                    )
                )
            if self.github is not None:
                await self.github.add_labels(
                    self.issue.number, [constants.LABEL_PLAN_REVIEW], task_id=self.task_id
                )
            self.recorder.event(
                EventKind.LOG,
                {
                    "awaiting": str(gate),
                    "announced_before": announced,
                    "bound_sha": self.spec_sha,
                    "governed_path": governed_path,
                    "owners": list(owners),
                },
                phase_id=phase_id,
            )
            while True:
                verdict = await queue.get()
                if verdict.counted and verdict.bound_sha == self.spec_sha:
                    break
        finally:
            self.approvals.unregister(self.task_id)
        if self.github is not None:
            await self.github.remove_label(
                self.issue.number, constants.LABEL_PLAN_REVIEW, task_id=self.task_id
            )
        self.recorder.finish(phase_id, PhaseStatus.PASS)
        return ApprovalOutcome(
            "approved",
            gate,
            self.spec_sha,
            actor=verdict.actor,
            approval_id=verdict.approval_id,
            waited=True,
        )
