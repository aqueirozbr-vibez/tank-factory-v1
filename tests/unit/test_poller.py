"""Unit 19: the reconciliation poller against recorded GitHub responses."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from orchestrator.github.client import GitHubClient
from orchestrator.intake.poller import Poller
from orchestrator.models.enums import TaskStatus, WorkspaceKind
from orchestrator.models.events import NormalisedEvent
from orchestrator.trace import TraceWriter, verify_chain

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "github"
NOW = datetime(2026, 10, 24, 12, 1, 0, tzinfo=UTC)
SHA_B = "b" * 40
MAIN_1 = "1" * 40
MAIN_2 = "2" * 40


def load(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class Scripted:
    def __init__(self) -> None:
        self.main_sha = MAIN_1
        self.fail_issue_events = False
        self.drop_reviews = False  # the transport dies on the reviews call
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/issues/events"):
            if self.fail_issue_events:
                return httpx.Response(500, json={"message": "boom"})
            return httpx.Response(200, json=load("issue_events.json"))
        if path.endswith("/pulls") and request.url.params.get("state") == "closed":
            return httpx.Response(200, json=load("pulls_closed.json"))
        if path.endswith("/pulls/431/reviews"):
            if self.drop_reviews:
                raise httpx.ReadError("connection reset by peer", request=request)
            return httpx.Response(200, json=load("reviews_431.json"))
        if path.endswith(f"/commits/{SHA_B}/check-runs"):
            return httpx.Response(200, json=load("check_runs.json"))
        if path.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": self.main_sha}})
        if "/compare/" in path:
            return httpx.Response(200, json=load("compare.json"))
        return httpx.Response(404, json={"message": f"no fixture for {path}"})


class StaticTokens:
    async def token_for(self, task_id: str) -> str:
        return "ghs_FAKEtoken"


async def no_sleep(_: float) -> None:
    return None


def make_poller(
    trace_writer: TraceWriter, scripted: Scripted
) -> tuple[Poller, asyncio.Queue[NormalisedEvent]]:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(scripted.handler), base_url="https://api.github.com"
    )
    client = GitHubClient(
        owner="acme", repo="atlas-api", tokens=StaticTokens(), http=http, sleep=no_sleep
    )
    inbox: asyncio.Queue[NormalisedEvent] = asyncio.Queue()
    return Poller(client, trace_writer, inbox, clock=lambda: NOW, interval_s=0.01), inbox


def open_pr_task(trace_writer: TraceWriter) -> None:
    trace_writer.insert_task(
        task_id="acme/atlas-api#418",
        issue_number=418,
        title="Invoice due date",
        branch="agent/418",
        workspace_kind=WorkspaceKind.WORKTREE,
        budget_usd=6.0,
        admitted_by="rsouza",
        config_sha="cfg1",
    )
    trace_writer.set_task_pr("acme/atlas-api#418", pr_number=431, head_sha=SHA_B)
    trace_writer.set_task_status("acme/atlas-api#418", TaskStatus.PR_OPEN)


async def test_first_poll_emits_every_kind_and_second_poll_only_duplicates(
    trace_writer: TraceWriter,
) -> None:
    open_pr_task(trace_writer)
    scripted = Scripted()
    poller, inbox = make_poller(trace_writer, scripted)

    report = await poller.poll_once()
    assert report.errors == []
    ids = sorted(e.github_event_id for e in report.new_events)
    assert ids == [
        "check_run:9001",
        "issue_event:5001",
        "issue_event:5002",
        f"pr_merged:431:{'c' * 40}",
        "review:77",
    ]
    by_id = {e.github_event_id: e for e in report.new_events}
    labeled = by_id["issue_event:5001"]
    assert (labeled.kind, labeled.issue_number, labeled.label, labeled.actor_login) == (
        "issue_labeled",
        418,
        "factory:ready",
        "rsouza",
    )
    assert by_id["issue_event:5002"].kind == "issue_unlabeled"
    merged = by_id[f"pr_merged:431:{'c' * 40}"]
    assert (merged.kind, merged.branch, merged.pr_number, merged.actor_login) == (
        "pull_request_merged",
        "agent/418",
        431,
        "rsouza",
    )
    review = by_id["review:77"]
    assert (review.review_state, review.actor_login, review.branch) == (
        "changes_requested",
        "mlima",
        "agent/418",
    )
    check = by_id["check_run:9001"]
    assert (check.conclusion, check.head_sha, check.pr_number) == ("failure", SHA_B, 431)
    assert all(e.source == "poll" for e in report.new_events)
    assert inbox.qsize() == 5
    assert trace_writer.get_poll_cursor("main_sha") == MAIN_1
    assert trace_writer.get_poll_cursor("events") == "2026-10-24T12:01:00.000+00:00"
    assert trace_writer.last_poll_at() == "2026-10-24T12:01:00.000+00:00"
    rows = trace_writer.db.fetchall("SELECT source FROM github_events")
    assert len(rows) == 5 and {r["source"] for r in rows} == {"poll"}

    second = await poller.poll_once()
    assert second.new_events == [] and second.duplicates == 5
    assert inbox.qsize() == 5
    polls = trace_writer.db.fetchall("SELECT payload_json FROM events WHERE kind = 'poll'")
    flags = [json.loads(r["payload_json"])["duplicate"] for r in polls]
    assert flags.count(False) == 5 and flags.count(True) == 5
    assert verify_chain(trace_writer.db).ok


async def test_main_head_change_emits_one_push(trace_writer: TraceWriter) -> None:
    scripted = Scripted()
    poller, _ = make_poller(trace_writer, scripted)
    await poller.poll_once()
    scripted.main_sha = MAIN_2
    report = await poller.poll_once()
    pushes = [e for e in report.new_events if e.kind == "push"]
    assert len(pushes) == 1
    push = pushes[0]
    assert push.github_event_id == f"push:{MAIN_2}"
    assert push.changed_paths == (".factory/roster.yaml", "README.md")
    assert push.head_sha == MAIN_2 and push.branch == "main"
    assert trace_writer.get_poll_cursor("main_sha") == MAIN_2
    compare_calls = [r for r in scripted.requests if "/compare/" in r.url.path]
    assert compare_calls[0].url.path.endswith(f"/compare/{MAIN_1}...{MAIN_2}")
    third = await poller.poll_once()
    assert not [e for e in third.new_events if e.kind == "push"]


async def test_failing_sub_poll_is_isolated(trace_writer: TraceWriter) -> None:
    scripted = Scripted()
    scripted.fail_issue_events = True
    poller, _ = make_poller(trace_writer, scripted)
    report = await poller.poll_once()
    assert len(report.errors) == 1 and report.errors[0].startswith("issue_events:")
    kinds = {e.kind for e in report.new_events}
    assert "pull_request_merged" in kinds and "issue_labeled" not in kinds
    error = trace_writer.db.fetchone("SELECT payload_json FROM events WHERE kind = 'error'")
    assert error is not None
    payload = json.loads(error["payload_json"])
    assert payload["poller"] == "issue_events" and payload["status"] == 500


async def test_run_stops_on_event(trace_writer: TraceWriter) -> None:
    poller, _ = make_poller(trace_writer, Scripted())
    stop = asyncio.Event()
    task = asyncio.create_task(poller.run(stop))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, 2)
    assert trace_writer.last_poll_at() is not None


async def test_a_dropped_connection_in_one_sub_poll_is_isolated(trace_writer: TraceWriter) -> None:
    """The 2026-10-25 crash: a transport error on the reviews call took the whole service down."""
    scripted = Scripted()
    scripted.drop_reviews = True
    open_pr_task(trace_writer)
    poller, _ = make_poller(trace_writer, scripted)
    report = await poller.poll_once()
    assert len(report.errors) == 1 and report.errors[0].startswith("reviews:")
    assert "pull_request_merged" in {e.kind for e in report.new_events}
    error = trace_writer.db.fetchone("SELECT payload_json FROM events WHERE kind = 'error'")
    assert error is not None
    payload = json.loads(error["payload_json"])
    assert payload["poller"] == "reviews" and payload["status"] == 0
    assert "transport" in payload["error"] and "ReadError" in payload["error"]
    assert trace_writer.last_poll_at() is not None  # the tick still completed


async def test_run_survives_a_failed_tick(
    trace_writer: TraceWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    poller, _ = make_poller(trace_writer, Scripted())
    original = poller.poll_once
    calls = {"n": 0}

    async def flaky() -> object:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database is locked")
        return await original()

    monkeypatch.setattr(poller, "poll_once", flaky)
    stop = asyncio.Event()
    task = asyncio.create_task(poller.run(stop))
    await asyncio.sleep(0.1)
    stop.set()
    await asyncio.wait_for(task, 2)
    assert calls["n"] >= 2 and trace_writer.last_poll_at() is not None
    errors = trace_writer.db.fetchall("SELECT payload_json FROM events WHERE kind = 'error'")
    ticks = [json.loads(str(r["payload_json"])) for r in errors]
    assert any(t.get("poller") == "tick" and "RuntimeError" in t["error"] for t in ticks)
