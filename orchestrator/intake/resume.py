"""Resume from blocked when a write-access user removes ``factory:needs-human`` (TRD §7.8).

The rule keys on ``blocked_reason``: protected path and dependency blocks
never resume; a budget block needs a prior raise; everything else is a
new attempt the runner enters at the right point, checking that the cause
changed where the cause was a human omission.
"""

from dataclasses import dataclass
from typing import Literal

from orchestrator import constants
from orchestrator.intake import messages
from orchestrator.intake.actors import actor_permission, has_write
from orchestrator.intake.ports import GitHubPort
from orchestrator.intake.scheduler import Scheduler
from orchestrator.models.enums import BlockedReason, EventKind, TaskStatus
from orchestrator.models.events import NormalisedEvent
from orchestrator.trace.writer import TraceWriter

ResumeKind = Literal[
    "requeued", "not_resumable", "budget_requires_raise", "refused_actor", "ignored"
]
NEVER = frozenset({BlockedReason.PROTECTED_PATH, BlockedReason.DEPENDENCY_CHANGE})
RULES: dict[BlockedReason, str] = {
    BlockedReason.OPEN_QUESTIONS: "re-enter plan once the cause changed",
    BlockedReason.MISSING_HOLDOUT: "re-enter plan once the cause changed",
    BlockedReason.HUMAN_DECISION_NEEDED: "re-enter plan once the spec changed",
    BlockedReason.SPEC_GATE: "re-enter plan once the cause changed",
    BlockedReason.REPAIR_CAP: "new attempt on the same branch with fresh caps",
    BlockedReason.REVISION_CAP: "new attempt on the same branch with fresh caps",
    BlockedReason.FULL_SUITE: "new attempt on the same branch with fresh caps",
    BlockedReason.TIMEOUT: "new attempt on the same branch with fresh caps",
    BlockedReason.BUDGET: "re-enter the stopped phase after a budget raise",
    BlockedReason.PROTECTED_PATH: "never resumes",
    BlockedReason.DEPENDENCY_CHANGE: "never resumes",
}


@dataclass(frozen=True)
class ResumeResult:
    kind: ResumeKind
    task_id: str | None
    reason: BlockedReason | None = None
    rule: str | None = None


class Resume:
    def __init__(self, *, writer: TraceWriter, github: GitHubPort, scheduler: Scheduler) -> None:
        self.writer = writer
        self.github = github
        self.scheduler = scheduler

    def _task_id(self, issue: int) -> str:
        installation = self.writer.get_installation()
        if installation is None:
            raise RuntimeError("the installation row is missing")
        return f"{installation['org']}/{installation['repo']}#{issue}"

    def budget_raised_since_block(self, task_id: str) -> bool:
        block = self.writer.db.fetchone(
            "SELECT seq FROM events WHERE task_id = ? AND kind = 'error'"
            " AND payload_json LIKE '%blocked_reason%' ORDER BY seq DESC LIMIT 1",
            (task_id,),
        )
        since = int(block["seq"]) if block is not None else 0
        raised = self.writer.db.fetchone(
            "SELECT 1 FROM events WHERE task_id = ? AND kind = 'control'"
            " AND payload_json LIKE '%budget_raise%' AND seq > ? LIMIT 1",
            (task_id, since),
        )
        return raised is not None

    async def apply(self, task_id: str, *, actor: str, reason: str) -> ResumeResult:
        """The console door: the actor already passed the authz table (unit 55)."""
        task = self.writer.get_task(task_id)
        if task is None or task["status"] != str(TaskStatus.BLOCKED) or not task["blocked_reason"]:
            return ResumeResult("ignored", task_id)
        blocked_reason = BlockedReason(str(task["blocked_reason"]))
        rule = RULES.get(blocked_reason, "new attempt")
        if blocked_reason in NEVER or (
            blocked_reason is BlockedReason.BUDGET and not self.budget_raised_since_block(task_id)
        ):
            kind: ResumeKind = (
                "not_resumable" if blocked_reason in NEVER else "budget_requires_raise"
            )
            return ResumeResult(kind, task_id, blocked_reason, rule)
        issue = int(task["issue_number"])
        if blocked_reason is BlockedReason.BUDGET:
            await self.github.remove_label(issue, constants.LABEL_BUDGET, task_id=task_id)
        await self.github.remove_label(issue, constants.LABEL_NEEDS_HUMAN, task_id=task_id)
        self.writer.requeue_task(task_id)
        self.writer.write_event(
            EventKind.CONTROL,
            actor,
            task_id=task_id,
            payload={
                "action": "resume",
                "blocked_reason": str(blocked_reason),
                "rule": rule,
                "reason": reason,
                "door": "console",
            },
        )
        await self.scheduler.dispatch()
        return ResumeResult("requeued", task_id, blocked_reason, rule)

    async def on_unlabeled(self, event: NormalisedEvent) -> ResumeResult:
        if event.issue_number is None:
            return ResumeResult("ignored", None)
        issue = event.issue_number
        task_id = self._task_id(issue)
        task = self.writer.get_task(task_id)
        if task is None or task["status"] != str(TaskStatus.BLOCKED) or not task["blocked_reason"]:
            self.writer.write_event(
                EventKind.LOG,
                "orchestrator",
                task_id=task_id if task is not None else None,
                payload={"resume": "ignored", "issue": issue, "status": task and task["status"]},
            )
            return ResumeResult("ignored", task_id)
        reason = BlockedReason(str(task["blocked_reason"]))
        rule = RULES.get(reason, "new attempt")
        permission = await actor_permission(
            self.writer, self.github, event.actor_login, task_id=task_id
        )
        if not has_write(permission):
            await self.github.create_comment(
                issue, messages.resume_refused(event.actor_login, permission), task_id=task_id
            )
            await self.github.add_labels(issue, [constants.LABEL_NEEDS_HUMAN], task_id=task_id)
            self.writer.write_event(
                EventKind.LOG,
                "orchestrator",
                task_id=task_id,
                payload={
                    "resume": "refused_actor",
                    "actor": event.actor_login,
                    "permission": permission,
                },
            )
            return ResumeResult("refused_actor", task_id, reason, rule)
        if reason in NEVER or (
            reason is BlockedReason.BUDGET and not self.budget_raised_since_block(task_id)
        ):
            summary = (
                "This block does not resume on its own."
                if reason in NEVER
                else "This block resumes only after a budget raise."
            )
            await self.github.create_comment(
                issue, messages.blocked(reason, phase=None, summary=summary), task_id=task_id
            )
            await self.github.add_labels(issue, [constants.LABEL_NEEDS_HUMAN], task_id=task_id)
            kind: ResumeKind = "not_resumable" if reason in NEVER else "budget_requires_raise"
            self.writer.write_event(
                EventKind.LOG,
                "orchestrator",
                task_id=task_id,
                payload={"resume": kind, "blocked_reason": str(reason), "actor": event.actor_login},
            )
            return ResumeResult(kind, task_id, reason, rule)
        if reason is BlockedReason.BUDGET:
            await self.github.remove_label(issue, constants.LABEL_BUDGET, task_id=task_id)
        self.writer.requeue_task(task_id)
        self.writer.write_event(
            EventKind.CONTROL,
            event.actor_login or "orchestrator",
            task_id=task_id,
            payload={
                "action": "resume",
                "blocked_reason": str(reason),
                "rule": rule,
                "github_event_id": event.github_event_id,
            },
        )
        await self.scheduler.dispatch()
        return ResumeResult("requeued", task_id, reason, rule)
