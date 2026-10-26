"""Admission and cancellation (PRD §Capability 1).

Only a human with write access grants readiness; admission is idempotent on
the task id; the row and the workspace exist before anything else happens;
nothing on GitHub changes before the row exists except the refusal itself,
which by definition creates no task.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from orchestrator import constants
from orchestrator.intake import messages
from orchestrator.intake.actors import actor_permission as lookup_permission
from orchestrator.intake.ports import GitHubPort
from orchestrator.intake.scheduler import Scheduler
from orchestrator.models.enums import EventKind, TaskStatus, WorkspaceKind
from orchestrator.models.events import NormalisedEvent
from orchestrator.models.settings import Settings
from orchestrator.supervisor import Supervisor
from orchestrator.trace.writer import TraceWriter
from orchestrator.workspace.worktree import create_worktree

Outcome = Literal[
    "admitted_running",
    "admitted_queued",
    "already_admitted",
    "readmitted",
    "refused_actor",
    "ignored",
]
ACTIVE = {TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.BLOCKED, TaskStatus.PR_OPEN}


@dataclass(frozen=True)
class AdmissionResult:
    outcome: Outcome
    task_id: str | None
    ahead: int = 0
    permission: str | None = None


class Admission:
    def __init__(
        self,
        *,
        writer: TraceWriter,
        github: GitHubPort,
        settings: Settings,
        repo_clone: Path,
        scheduler: Scheduler,
    ) -> None:
        self.writer = writer
        self.github = github
        self.settings = settings
        self.repo_clone = repo_clone
        self.scheduler = scheduler

    def _task_id(self, issue: int) -> str:
        installation = self.writer.get_installation()
        if installation is None:
            raise RuntimeError("the installation row is missing")
        return f"{installation['org']}/{installation['repo']}#{issue}"

    async def actor_permission(self, login: str | None, *, task_id: str) -> str:
        return await lookup_permission(self.writer, self.github, login, task_id=task_id)

    async def admit(self, event: NormalisedEvent) -> AdmissionResult:
        if event.issue_number is None:
            return AdmissionResult("ignored", None)
        issue = event.issue_number
        task_id = self._task_id(issue)
        permission = await self.actor_permission(event.actor_login, task_id=task_id)
        if permission not in constants.WRITE_PERMISSIONS:
            await self.github.remove_label(issue, constants.LABEL_READY, task_id=task_id)
            await self.github.create_comment(
                issue, messages.refused_actor(event.actor_login, permission), task_id=task_id
            )
            self.writer.write_event(
                EventKind.LOG,
                "orchestrator",
                payload={
                    "admission": "refused_actor",
                    "issue": issue,
                    "actor": event.actor_login,
                    "permission": permission,
                },
            )
            return AdmissionResult("refused_actor", None, permission=permission)

        existing = self.writer.get_task(task_id)
        if existing is not None:
            status = TaskStatus(str(existing["status"]))
            if status in ACTIVE:
                self.writer.write_event(
                    EventKind.LOG,
                    "orchestrator",
                    task_id=task_id,
                    payload={"admission": "duplicate", "status": str(status)},
                )
                return AdmissionResult("already_admitted", task_id)
            if status is TaskStatus.MERGED:
                self.writer.write_event(
                    EventKind.LOG,
                    "orchestrator",
                    task_id=task_id,
                    payload={"admission": "ignored_merged"},
                )
                return AdmissionResult("ignored", task_id)
            self.writer.requeue_task(task_id)
            create_worktree(self.repo_clone, issue=issue, worktrees_dir=self.settings.worktrees_dir)
            self.writer.write_event(
                EventKind.LOG,
                "orchestrator",
                task_id=task_id,
                payload={"admission": "readmitted", "actor": event.actor_login},
            )
            started = await self.scheduler.dispatch()
            if task_id not in started:
                await self._queue_comment(issue, task_id)
            return AdmissionResult("readmitted", task_id)

        installation = self.writer.get_installation()
        assert installation is not None
        issue_data = await self.github.get_issue(issue, task_id=task_id)
        title = str(issue_data.get("title") or "")[:200] or None
        created = self.writer.insert_task(
            task_id=task_id,
            issue_number=issue,
            title=title,
            branch=f"{constants.BRANCH_PREFIX}{issue}",
            workspace_kind=WorkspaceKind(self.settings.workspace),
            budget_usd=float(installation["per_task_budget_usd"]),
            admitted_by=str(event.actor_login),
            config_sha=str(installation["config_sha"]),
        )
        if not created:  # a concurrent admission won the race
            return AdmissionResult("already_admitted", task_id)
        worktree = create_worktree(
            self.repo_clone, issue=issue, worktrees_dir=self.settings.worktrees_dir
        )
        self.writer.write_event(
            EventKind.LOG,
            "orchestrator",
            task_id=task_id,
            payload={
                "admission": "queued",
                "branch": worktree.branch,
                "workspace": str(worktree.path),
                "admitted_by": event.actor_login,
                "github_event_id": event.github_event_id,
            },
        )
        started = await self.scheduler.dispatch()
        if task_id in started:
            return AdmissionResult("admitted_running", task_id)
        ahead = await self._queue_comment(issue, task_id)
        return AdmissionResult("admitted_queued", task_id, ahead=ahead)

    async def _queue_comment(self, issue: int, task_id: str) -> int:
        ahead = self.writer.queue_position(task_id)
        await self.github.create_comment(
            issue, messages.queued(ahead, self.scheduler.concurrency()), task_id=task_id
        )
        return ahead


class Cancellation:
    def __init__(
        self,
        *,
        writer: TraceWriter,
        github: GitHubPort,
        supervisor: Supervisor,
        scheduler: Scheduler | None = None,
    ) -> None:
        self.writer = writer
        self.github = github
        self.supervisor = supervisor
        self.scheduler = scheduler

    async def cancel(self, event: NormalisedEvent, *, reason: str | None = None) -> bool:
        if event.issue_number is None:
            return False
        installation = self.writer.get_installation()
        if installation is None:
            return False
        task_id = f"{installation['org']}/{installation['repo']}#{event.issue_number}"
        return await self.cancel_task(
            task_id,
            actor=event.actor_login or "orchestrator",
            reason=reason or f"{constants.LABEL_READY} removed",
        )

    async def cancel_task(self, task_id: str, *, actor: str, reason: str) -> bool:
        task = self.writer.get_task(task_id)
        if task is None or task["status"] in ("cancelled", "merged"):
            return False
        last_phase = self.writer.last_completed_phase(task_id)
        was_live = await self.supervisor.cancel(task_id, reason=reason)
        self.writer.close_open_work(task_id, error="cancelled")
        self.writer.set_task_status(task_id, TaskStatus.CANCELLED)
        if self.scheduler is not None:
            await self.scheduler.dispatch()  # the freed slot goes to the next queued task
        self.writer.write_event(
            EventKind.CONTROL,
            actor,
            task_id=task_id,
            payload={
                "action": "cancel",
                "reason": reason,
                "was_live": was_live,
                "last_completed_phase": last_phase,
            },
        )
        await self.github.create_comment(
            int(task["issue_number"]), messages.cancelled(last_phase), task_id=task_id
        )
        return True
