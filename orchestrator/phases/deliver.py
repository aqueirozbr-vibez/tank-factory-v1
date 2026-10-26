"""Provisional delivery: push, one pull request, `factory:review`, `pr_open` (PRD §Capability 5).

The push and the PR run under the installation's App token; the PR body
is rendered from trace rows; re-entry reuses ``tasks.pr_number`` so a
second PR for the same task is impossible (TRD §7.7). The merge observer
closes the task when GitHub reports the merge.
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from orchestrator import constants
from orchestrator.instruments.verdict import VerdictScore
from orchestrator.models.enums import (
    BlockedReason,
    EventKind,
    OutputType,
    PhaseKind,
    PhaseName,
    PhaseStatus,
    TaskStatus,
)
from orchestrator.models.events import NormalisedEvent
from orchestrator.phases.build import BuildDeps
from orchestrator.phases.recorder import PhaseRecorder
from orchestrator.templates.pr_body import pr_body_fields, render_pr_body
from orchestrator.trace.writer import TraceWriter
from orchestrator.workspace.git import GitError, head_sha, push_argv, push_branch
from orchestrator.workspace.worktree import Worktree


class DeliveryPort(Protocol):
    async def list_pulls_for_branch(self, head: str, *, task_id: str) -> list[dict[str, Any]]: ...

    async def create_pull(
        self, *, title: str, head: str, base: str, body: str, task_id: str
    ) -> dict[str, Any]: ...

    async def add_labels(self, number: int, labels: Sequence[str], *, task_id: str) -> None: ...

    async def request_reviewers(
        self, number: int, reviewers: Sequence[str], *, task_id: str, teams: Sequence[str] = ()
    ) -> None: ...


@dataclass(frozen=True)
class DeliverOutcome:
    status: Literal["pr_open", "merged", "blocked"]
    run_id: str
    pr_number: int | None
    head_sha: str | None
    created: bool
    pushed: bool
    blocked_reason: BlockedReason | None = None
    evidence: str = ""

    @property
    def delivered(self) -> bool:
        return self.status == "pr_open"


def split_owners(owners: Sequence[str]) -> tuple[list[str], list[str]]:
    """``@login`` → reviewers; ``@org/team`` → team slugs; anything else is skipped."""
    logins: list[str] = []
    teams: list[str] = []
    for owner in owners:
        if not owner.startswith("@"):
            continue
        name = owner[1:]
        if "/" in name:
            teams.append(name.split("/", 1)[1])
        else:
            logins.append(name)
    return logins, teams


def pr_title(writer: TraceWriter, *, run_id: str, fallback: str) -> str:
    text = writer.latest_envelope(run_id, OutputType.BUILD)
    if text:
        try:
            message = json.loads(text).get("commit_message")
        except ValueError:
            message = None
        if isinstance(message, str) and message.strip():
            return message.strip().splitlines()[0][:200]
    return fallback


class DeliverPhase:
    def __init__(
        self,
        deps: BuildDeps,
        *,
        task_id: str,
        worktree: Worktree,
        spec_rel: str,
        run_id: str,
        github: DeliveryPort,
        base_branch: str,
        verdict: VerdictScore | None = None,
        token: str | None = None,
        owners: Sequence[str] = (),
        attempt: int = 1,
        seq_start: int | None = None,
    ) -> None:
        self.deps = deps
        self.writer = deps.writer
        self.task_id = task_id
        self.worktree = worktree
        self.spec_rel = spec_rel
        self.run_id = run_id
        self.github = github
        self.base_branch = base_branch
        self.verdict = verdict
        self.token = token
        self.owners = tuple(owners)
        self.attempt = attempt
        start = self.writer.last_phase_seq(run_id) if seq_start is None else seq_start
        self.recorder = PhaseRecorder(self.writer, task_id=task_id, run_id=run_id, seq_start=start)

    def _spec_text(self) -> str:
        path = self.worktree.path.joinpath(*self.spec_rel.split("/"))
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    async def _push(self, phase_id: str) -> str | None:
        """The head sha when the branch is on the remote; ``None`` after the retries."""
        shown = " ".join(push_argv("origin", self.worktree.branch, None))
        for attempt in range(constants.INFRA_RETRIES + 1):
            try:
                sha = push_branch(self.worktree.path, self.worktree.branch, token=self.token)
            except GitError as exc:
                self.recorder.event(
                    EventKind.ERROR,
                    {
                        "push": "failed",
                        "command": shown,
                        "error": str(exc)[:500],
                        "try": attempt + 1,
                    },
                    phase_id=phase_id,
                )
                if attempt < constants.INFRA_RETRIES:
                    await self.deps.sleep(constants.INFRA_BACKOFF_S[attempt])
                    continue
                return None
            self.recorder.event(
                EventKind.LOG,
                {"push": self.worktree.branch, "sha": sha, "command": shown, "identity": "app"},
                phase_id=phase_id,
            )
            return sha
        return None

    async def run(self) -> DeliverOutcome:
        task = self.writer.get_task(self.task_id)
        assert task is not None
        if task["status"] == str(TaskStatus.MERGED):
            return DeliverOutcome(
                "merged", self.run_id, task["pr_number"], task["pr_head_sha"], False, False
            )
        phase_id = self.recorder.start(
            PhaseName.DELIVER, PhaseKind.CODE, "orchestrator", attempt=self.attempt, timeout_s=600
        )
        sha = await self._push(phase_id)
        if sha is None:
            evidence = f"push of {self.worktree.branch} failed {constants.INFRA_RETRIES + 1} times"
            self.recorder.finish(phase_id, PhaseStatus.FAIL, error="push_failed")
            self.writer.set_task_status(
                self.task_id, TaskStatus.BLOCKED, blocked_reason=BlockedReason.TIMEOUT
            )
            self.recorder.event(
                EventKind.ERROR,
                {"blocked_reason": str(BlockedReason.TIMEOUT), "evidence": evidence},
                phase_id=phase_id,
            )
            return DeliverOutcome(
                "blocked", self.run_id, None, None, False, False, BlockedReason.TIMEOUT, evidence
            )

        created = False
        pr_number = task["pr_number"]
        if pr_number is None:
            existing = await self.github.list_pulls_for_branch(
                self.worktree.branch, task_id=self.task_id
            )
            open_ones = [p for p in existing if p.get("state") == "open"]
            chosen = (open_ones or existing)[:1]
            if chosen:
                pr_number = int(chosen[0]["number"])
                self.recorder.event(
                    EventKind.LOG,
                    {"pr_adopted": pr_number, "state": chosen[0].get("state")},
                    phase_id=phase_id,
                )
            else:
                fallback = f"factory: {task['title'] or self.task_id} (#{task['issue_number']})"
                body = render_pr_body(
                    pr_body_fields(
                        self.writer,
                        task_id=self.task_id,
                        run_id=self.run_id,
                        spec_rel=self.spec_rel,
                        spec_text=self._spec_text(),
                        branch=self.worktree.branch,
                        sha=sha,
                    )
                )
                pull = await self.github.create_pull(
                    title=pr_title(self.writer, run_id=self.run_id, fallback=fallback),
                    head=self.worktree.branch,
                    base=self.base_branch,
                    body=body,
                    task_id=self.task_id,
                )
                pr_number = int(pull["number"])
                created = True
                self.recorder.event(
                    EventKind.LOG,
                    {"pr_opened": pr_number, "url": pull.get("html_url"), "identity": "app"},
                    phase_id=phase_id,
                )
                await self.github.add_labels(
                    int(task["issue_number"]), [constants.LABEL_REVIEW], task_id=self.task_id
                )
                logins, teams = split_owners(self.owners)
                if logins or teams:
                    await self.github.request_reviewers(
                        pr_number, logins, task_id=self.task_id, teams=teams
                    )
        self.writer.set_task_pr(self.task_id, pr_number=int(pr_number), head_sha=sha)
        self.writer.set_task_status(self.task_id, TaskStatus.PR_OPEN)
        self.recorder.event(
            EventKind.HANDOFF,
            {"pr": int(pr_number), "head_sha": sha, "created": created},
            phase_id=phase_id,
        )
        self.recorder.finish(phase_id, PhaseStatus.PASS)
        if self.writer.open_phase(self.task_id, PhaseName.CI_WAIT) is None:
            # left running until CI, a review or the merge closes it (TRD §7.5, §7.8)
            self.recorder.start(
                PhaseName.CI_WAIT,
                PhaseKind.ENGINEER,
                "github",
                attempt=self.attempt,
                timeout_s=constants.CI_WAIT_TIMEOUT_S,
            )
        return DeliverOutcome("pr_open", self.run_id, int(pr_number), sha, created, True)


class MergeObserver:
    """``pull_request_merged`` → ``tasks.status = merged`` (PRD §Capability 5)."""

    def __init__(
        self, writer: TraceWriter, *, on_merge: Callable[[str], None] | None = None
    ) -> None:
        self.writer = writer
        self.on_merge = on_merge  # the KPI materialiser and the audit draw (unit 48)

    async def on_merged(self, event: NormalisedEvent) -> bool:
        task = None
        if event.pr_number is not None:
            task = self.writer.task_for_pr(event.pr_number)
        if task is None and event.branch:
            task = self.writer.task_for_branch(event.branch)
        if task is None or event.kind != "pull_request_merged":
            self.writer.write_event(
                EventKind.LOG,
                "orchestrator",
                payload={
                    "merge": "ignored",
                    "kind": event.kind,
                    "pr": event.pr_number,
                    "branch": event.branch,
                    "known_task": task is not None,
                },
            )
            return False
        task_id = str(task["task_id"])
        if task["status"] == str(TaskStatus.MERGED):
            return True
        self.writer.finish_open_phase(task_id, PhaseName.CI_WAIT, PhaseStatus.PASS)
        self.writer.set_task_status(task_id, TaskStatus.MERGED)
        self.writer.write_event(
            EventKind.LOG,
            "orchestrator",
            task_id=task_id,
            payload={
                "merged": event.pr_number,
                "head_sha": event.head_sha,
                "by": event.actor_login,
                "github_event_id": event.github_event_id,
            },
        )
        if self.on_merge is not None:
            self.on_merge(task_id)
        return True


__all__ = [
    "DeliverOutcome",
    "DeliverPhase",
    "DeliveryPort",
    "MergeObserver",
    "head_sha",
    "pr_title",
    "split_owners",
]
