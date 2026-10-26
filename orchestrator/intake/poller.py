"""The 60 s reconciliation poller: webhooks and polls are one event stream (TRD §4.5)."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from orchestrator import constants
from orchestrator.clock import utc_now
from orchestrator.github.client import GitHubApiError, GitHubClient
from orchestrator.intake.webhook import record_delivery
from orchestrator.models.enums import EventKind
from orchestrator.models.events import NormalisedEvent
from orchestrator.trace.writer import TraceWriter

POLL_TOKEN_KEY = "poller"
OVERLAP = timedelta(seconds=120)
DEFAULT_LOOKBACK = timedelta(hours=24)
Clock = Callable[[], datetime]


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="milliseconds")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass
class PollReport:
    started_at: str
    new_events: list[NormalisedEvent] = field(default_factory=list)
    duplicates: int = 0
    errors: list[str] = field(default_factory=list)


class Poller:
    def __init__(
        self,
        client: GitHubClient,
        writer: TraceWriter,
        inbox: asyncio.Queue[NormalisedEvent],
        *,
        interval_s: float = constants.POLL_INTERVAL_S,
        clock: Clock = lambda: datetime.now(UTC),
        main_branch: str = "main",
    ) -> None:
        self.client = client
        self.writer = writer
        self.inbox = inbox
        self.interval_s = interval_s
        self.clock = clock
        self.main_branch = main_branch

    # --- sub-polls ----------------------------------------------------------------

    async def _issue_events(self, since: datetime) -> list[NormalisedEvent]:
        found: list[NormalisedEvent] = []
        for item in await self.client.list_issue_events(task_id=POLL_TOKEN_KEY):
            created = _parse(str(item["created_at"]))
            if created <= since:
                continue
            action = str(item.get("event", ""))
            if action not in ("labeled", "unlabeled"):
                continue
            issue = item.get("issue") or {}
            found.append(
                NormalisedEvent(
                    kind="issue_labeled" if action == "labeled" else "issue_unlabeled",
                    source="poll",
                    github_event_id=f"issue_event:{item['id']}",
                    event_type="issues",
                    action=action,
                    issue_number=int(issue["number"]) if issue.get("number") else None,
                    actor_login=(item.get("actor") or {}).get("login"),
                    label=(item.get("label") or {}).get("name"),
                    occurred_at=str(item["created_at"]),
                    received_at=utc_now(),
                )
            )
        return found

    async def _merged_pulls(self, since: datetime) -> list[NormalisedEvent]:
        found: list[NormalisedEvent] = []
        pulls = await self.client.list_pulls(state="closed", sort="updated", task_id=POLL_TOKEN_KEY)
        for pr in pulls:
            merged_at = pr.get("merged_at")
            head = pr.get("head") or {}
            if not merged_at or _parse(str(merged_at)) <= since:
                continue
            if not str(head.get("ref", "")).startswith(constants.BRANCH_PREFIX):
                continue
            found.append(
                NormalisedEvent(
                    kind="pull_request_merged",
                    source="poll",
                    github_event_id=f"pr_merged:{pr['number']}:{pr.get('merge_commit_sha')}",
                    event_type="pull_request",
                    action="closed",
                    pr_number=int(pr["number"]),
                    branch=str(head.get("ref")),
                    head_sha=head.get("sha"),
                    actor_login=(pr.get("merged_by") or {}).get("login"),
                    received_at=utc_now(),
                )
            )
        return found

    async def _reviews(self, since: datetime) -> list[NormalisedEvent]:
        found: list[NormalisedEvent] = []
        for row in self.writer.open_pull_requests():
            number = int(row["pr_number"])
            for review in await self.client.list_reviews(number, task_id=POLL_TOKEN_KEY):
                submitted = review.get("submitted_at")
                state = str(review.get("state", "")).lower()
                if not submitted or _parse(str(submitted)) <= since:
                    continue
                if state not in ("changes_requested", "approved"):
                    continue
                found.append(
                    NormalisedEvent(
                        kind="review_submitted",
                        source="poll",
                        github_event_id=f"review:{review['id']}",
                        event_type="pull_request_review",
                        action="submitted",
                        pr_number=number,
                        branch=str(row["branch"]),
                        review_state=state,
                        actor_login=(review.get("user") or {}).get("login"),
                        received_at=utc_now(),
                    )
                )
        return found

    async def _check_runs(self, since: datetime) -> list[NormalisedEvent]:
        found: list[NormalisedEvent] = []
        for row in self.writer.open_pull_requests():
            head_sha = row["pr_head_sha"]
            if not head_sha:
                continue
            for run in await self.client.list_check_runs(str(head_sha), task_id=POLL_TOKEN_KEY):
                completed = run.get("completed_at")
                if run.get("status") != "completed" or not completed:
                    continue
                if _parse(str(completed)) <= since:
                    continue
                found.append(
                    NormalisedEvent(
                        kind="check_run_completed",
                        source="poll",
                        github_event_id=f"check_run:{run['id']}",
                        event_type="check_run",
                        action="completed",
                        pr_number=int(row["pr_number"]),
                        branch=str(row["branch"]),
                        head_sha=str(head_sha),
                        conclusion=run.get("conclusion"),
                        received_at=utc_now(),
                    )
                )
        return found

    async def _push_main(self) -> list[NormalisedEvent]:
        sha = await self.client.get_branch_sha(self.main_branch, task_id=POLL_TOKEN_KEY)
        if sha is None:
            return []
        last = self.writer.get_poll_cursor("main_sha")
        if last == sha:
            return []
        self.writer.set_poll_cursor("main_sha", sha)
        if last is None:
            return []
        files = await self.client.compare_files(last, sha, task_id=POLL_TOKEN_KEY)
        return [
            NormalisedEvent(
                kind="push",
                source="poll",
                github_event_id=f"push:{sha}",
                event_type="push",
                branch=self.main_branch,
                head_sha=sha,
                changed_paths=tuple(files),
                received_at=utc_now(),
            )
        ]

    # --- one tick -----------------------------------------------------------------

    async def poll_once(self) -> PollReport:
        now = self.clock()
        report = PollReport(started_at=_iso(now))
        cursor = self.writer.get_poll_cursor("events")
        since = (_parse(cursor) if cursor else now - DEFAULT_LOOKBACK) - OVERLAP
        events: list[NormalisedEvent] = []
        sub_polls: list[tuple[str, Callable[[], Awaitable[list[NormalisedEvent]]]]] = [
            ("issue_events", lambda: self._issue_events(since)),
            ("merged_pulls", lambda: self._merged_pulls(since)),
            ("reviews", lambda: self._reviews(since)),
            ("check_runs", lambda: self._check_runs(since)),
            ("push_main", self._push_main),
        ]
        for name, fn in sub_polls:
            try:
                events.extend(await fn())
            except Exception as exc:  # one sub-poll's failure never costs the others their tick
                report.errors.append(f"{name}: {exc}")
                self.writer.write_event(
                    EventKind.ERROR,
                    "orchestrator",
                    payload={
                        "poller": name,
                        "status": exc.status if isinstance(exc, GitHubApiError) else None,
                        "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                    },
                )
        for event in events:
            if record_delivery(self.writer, event):
                report.new_events.append(event)
                if event.kind != "ignored":
                    self.inbox.put_nowait(event)
            else:
                report.duplicates += 1
        self.writer.set_poll_cursor("events", _iso(now))
        self.writer.set_last_poll(_iso(now))
        return report

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.poll_once()
            except Exception as exc:  # a failed tick is recorded; the loop outlives it
                self.writer.write_event(
                    EventKind.ERROR,
                    "orchestrator",
                    payload={"poller": "tick", "error": f"{type(exc).__name__}: {str(exc)[:300]}"},
                )
            try:
                await asyncio.wait_for(stop.wait(), self.interval_s)
            except TimeoutError:
                continue
