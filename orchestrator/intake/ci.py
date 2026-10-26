"""CI and review re-entry: a failing check or a change request re-enters the build (PRD §Cap. 5).

The task is found by PR number or branch; only a `pr_open` task on the
signalled head sha re-enters, once per push; the `ci_wait` row fails with
the cause and a `ci_reentry` control event drives the runner's next entry.
"""

import sqlite3
from dataclasses import dataclass
from typing import Literal

from orchestrator.intake.scheduler import Scheduler
from orchestrator.models.enums import EventKind, PhaseName, PhaseStatus, TaskStatus
from orchestrator.models.events import NormalisedEvent
from orchestrator.trace.writer import TraceWriter

CI_FAILURES = frozenset({"failure", "timed_out", "cancelled", "action_required"})
ReentryKind = Literal[
    "requeued", "ignored", "ignored_not_pr_open", "ignored_stale", "ignored_pending"
]


@dataclass(frozen=True)
class ReentryResult:
    kind: ReentryKind
    task_id: str | None
    cause: str | None = None


def pending_reentry(writer: TraceWriter, task_id: str) -> sqlite3.Row | None:
    """The latest ``ci_reentry`` control newer than the last delivery handoff, if any."""
    control = writer.latest_control(task_id, "ci_reentry")
    if control is None:
        return None
    handoff = writer.latest_handoff_seq(task_id, "pr")
    return control if handoff is None or int(control["seq"]) > handoff else None


class CiReentry:
    def __init__(self, *, writer: TraceWriter, scheduler: Scheduler) -> None:
        self.writer = writer
        self.scheduler = scheduler

    def _task(self, event: NormalisedEvent) -> sqlite3.Row | None:
        task = self.writer.task_for_pr(event.pr_number) if event.pr_number is not None else None
        if task is None and event.branch:
            task = self.writer.task_for_branch(event.branch)
        return task

    async def on_ci_failed(self, event: NormalisedEvent) -> ReentryResult:
        if event.conclusion not in CI_FAILURES:
            return ReentryResult("ignored", None)
        cause = f"ci_{event.conclusion}"
        detail = (
            f"{event.event_type} on {event.head_sha or 'the PR head'} concluded {event.conclusion}"
        )
        return await self._reenter(event, cause=cause, detail=detail)

    async def on_changes_requested(self, event: NormalisedEvent) -> ReentryResult:
        if event.review_state != "changes_requested":
            return ReentryResult("ignored", None)
        who = event.actor_login or "a reviewer"
        detail = f"review by {who} requested changes on PR #{event.pr_number}"
        return await self._reenter(event, cause="changes_requested", detail=detail)

    async def _reenter(self, event: NormalisedEvent, *, cause: str, detail: str) -> ReentryResult:
        task = self._task(event)
        if task is None or task["status"] != str(TaskStatus.PR_OPEN):
            self.writer.write_event(
                EventKind.LOG,
                "orchestrator",
                task_id=None if task is None else str(task["task_id"]),
                payload={
                    "reentry": "ignored_not_pr_open",
                    "cause": cause,
                    "pr": event.pr_number,
                    "branch": event.branch,
                    "status": None if task is None else task["status"],
                },
            )
            return ReentryResult(
                "ignored_not_pr_open", None if task is None else str(task["task_id"]), cause
            )
        task_id = str(task["task_id"])
        recorded = task["pr_head_sha"]
        if event.head_sha and recorded and event.head_sha != recorded:
            self.writer.write_event(
                EventKind.LOG,
                "orchestrator",
                task_id=task_id,
                payload={
                    "reentry": "ignored_stale",
                    "cause": cause,
                    "sha": event.head_sha,
                    "head": recorded,
                },
            )
            return ReentryResult("ignored_stale", task_id, cause)
        if pending_reentry(self.writer, task_id) is not None:
            self.writer.write_event(
                EventKind.LOG,
                "orchestrator",
                task_id=task_id,
                payload={
                    "reentry": "ignored_pending",
                    "cause": cause,
                    "github_event_id": event.github_event_id,
                },
            )
            return ReentryResult("ignored_pending", task_id, cause)
        self.writer.finish_open_phase(task_id, PhaseName.CI_WAIT, PhaseStatus.FAIL, error=cause)
        self.writer.write_event(
            EventKind.CONTROL,
            event.actor_login or "github",
            task_id=task_id,
            payload={
                "action": "ci_reentry",
                "cause": cause,
                "detail": detail,
                "pr": event.pr_number,
                "head_sha": event.head_sha,
                "github_event_id": event.github_event_id,
            },
        )
        self.writer.requeue_task(task_id)
        await self.scheduler.dispatch()
        return ReentryResult("requeued", task_id, cause)
