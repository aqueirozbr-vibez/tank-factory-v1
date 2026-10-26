"""Unit 30: push to a bare remote, one pull request, factory:review, pr_open, merge."""

import base64
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from orchestrator.intake import Handlers, Router
from orchestrator.issue_to_pr import PipelineOutcome, deliver, run_build_and_review
from orchestrator.models.enums import BlockedReason
from orchestrator.models.events import NormalisedEvent
from orchestrator.phases.deliver import DeliverPhase, MergeObserver, split_owners
from orchestrator.trace import TraceWriter, verify_chain
from orchestrator.workspace.git import push_argv, run_git
from orchestrator.workspace.worktree import Worktree
from tests.unit.test_build_phase import GREEN, SPEC_REL, TASK_ID, deps_for, phases, worktree_for
from tests.unit.test_review_phase import ACCEPTED


class FakePulls:
    def __init__(self, existing: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[tuple[str, object]] = []
        self.pulls: list[dict[str, Any]] = list(existing or [])
        self.next_number = 431

    async def list_pulls_for_branch(self, head: str, *, task_id: str) -> list[dict[str, Any]]:
        self.calls.append(("list_pulls", head))
        return [p for p in self.pulls if p["head"]["ref"] == head]

    async def create_pull(
        self, *, title: str, head: str, base: str, body: str, task_id: str
    ) -> dict[str, Any]:
        number = self.next_number
        self.next_number += 1
        pull = {
            "number": number,
            "state": "open",
            "title": title,
            "head": {"ref": head, "sha": "b" * 40},
            "base": {"ref": base},
            "html_url": f"https://github.com/acme/atlas-api/pull/{number}",
            "body": body,
        }
        self.pulls.append(pull)
        self.calls.append(("create_pull", (title, head, base)))
        return pull

    async def add_labels(self, number: int, labels: Sequence[str], *, task_id: str) -> None:
        self.calls.append(("add_labels", (number, tuple(labels))))

    async def request_reviewers(
        self, number: int, reviewers: Sequence[str], *, task_id: str, teams: Sequence[str] = ()
    ) -> None:
        self.calls.append(("request_reviewers", (number, tuple(reviewers), tuple(teams))))

    def created(self) -> list[tuple[str, object]]:
        return [c for c in self.calls if c[0] == "create_pull"]


def add_remote(product: Path, tmp_path: Path) -> Path:
    bare = tmp_path / "remote.git"
    run_git(["init", "--bare", "-q", str(bare)], cwd=tmp_path)
    run_git(["remote", "add", "origin", str(bare)], cwd=product)
    return bare


async def accepted_pipeline(
    trace_writer: TraceWriter, product: Path, tmp_path: Path
) -> tuple[PipelineOutcome, Worktree, Any]:
    deps, _, recorder = deps_for(trace_writer, [GREEN, ACCEPTED])
    wt = worktree_for(product, tmp_path)
    outcome = await run_build_and_review(deps, task_id=TASK_ID, worktree=wt, spec_rel=SPEC_REL)
    assert outcome.accepted
    return outcome, wt, (deps, recorder)


def merged_event(pr: int, *, branch: str = "agent/418", merged: bool = True) -> NormalisedEvent:
    return NormalisedEvent(
        kind="pull_request_merged" if merged else "pull_request_closed",
        source="webhook",
        github_event_id=f"m-{pr}",
        event_type="pull_request",
        action="closed",
        pr_number=pr,
        branch=branch,
        head_sha="b" * 40,
        actor_login="rsouza",
        received_at="2026-10-24T12:00:00.000+00:00",
    )


def test_push_argv_keeps_the_token_out_of_the_url_and_split_owners() -> None:
    plain = push_argv("origin", "agent/418", None)
    assert plain == ["push", "origin", "agent/418:agent/418"]
    with_token = push_argv("origin", "agent/418", "t0k")
    expected = base64.b64encode(b"x-access-token:t0k").decode("ascii")
    assert with_token[:2] == ["-c", f"http.extraheader=AUTHORIZATION: basic {expected}"]
    assert with_token[2:] == plain and "t0k" not in " ".join(with_token[2:])
    assert split_owners(("@mlima", "@acme/platform", "dev@example.test")) == (
        ["mlima"],
        ["platform"],
    )


async def test_accepted_task_is_pushed_and_one_pr_opens(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    bare = add_remote(product, tmp_path)
    outcome, wt, (deps, _) = await accepted_pipeline(trace_writer, product, tmp_path)
    github = FakePulls()
    delivered = await deliver(
        deps,
        task_id=TASK_ID,
        worktree=wt,
        spec_rel=SPEC_REL,
        outcome=outcome,
        github=github,
        base_branch="main",
        owners=("@mlima", "@acme/platform"),
    )
    assert delivered.delivered and delivered.created and delivered.pushed
    assert delivered.pr_number == 431 and delivered.head_sha == outcome.build.commit_sha
    remote_sha = run_git(["rev-parse", "refs/heads/agent/418"], cwd=bare).stdout.strip()
    assert remote_sha == outcome.build.commit_sha
    assert github.created()[0][1] == (
        "feat(billing): invoice due_date (#418)",
        "agent/418",
        "main",
    )
    body = github.pulls[0]["body"]
    assert "https://github.com/acme/atlas-api/blob/agent/418/specs/418-invoice-due-date.md" in body
    assert (
        "| focused_tests | 1 | pass |" in body
        and "| review | reviewer | fake | gpt-5-codex |" in body
    )
    assert "verdict `accepted`, conformance 100.0" in body and "Total cost: $0.6100" in body
    assert ("add_labels", (418, ("factory:review",))) in github.calls
    assert ("request_reviewers", (431, ("mlima",), ("platform",))) in github.calls
    task = trace_writer.get_task(TASK_ID)
    assert task is not None and task["status"] == "pr_open"
    assert task["pr_number"] == 431 and task["pr_head_sha"] == outcome.build.commit_sha
    assert phases(trace_writer, outcome.run_id)[-2:] == [
        ("deliver", 1, "pass"),
        ("ci_wait", 1, "running"),
    ]
    handoff = trace_writer.db.fetchone(
        "SELECT payload_json FROM events WHERE kind = 'handoff' ORDER BY seq DESC LIMIT 1"
    )
    assert handoff is not None and '"pr": 431' in handoff["payload_json"].replace(":431", ": 431")
    assert verify_chain(trace_writer.db).ok

    again = await deliver(
        deps,
        task_id=TASK_ID,
        worktree=wt,
        spec_rel=SPEC_REL,
        outcome=outcome,
        github=github,
        base_branch="main",
    )
    assert again.delivered and not again.created and again.pr_number == 431
    assert len(github.created()) == 1 and len(trace_writer.open_pull_requests()) == 1


async def test_existing_pr_for_the_branch_is_adopted(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    add_remote(product, tmp_path)
    outcome, wt, (deps, _) = await accepted_pipeline(trace_writer, product, tmp_path)
    existing = {"number": 77, "state": "open", "head": {"ref": "agent/418", "sha": "c" * 40}}
    github = FakePulls([existing])
    delivered = await DeliverPhase(
        deps,
        task_id=TASK_ID,
        worktree=wt,
        spec_rel=SPEC_REL,
        run_id=outcome.run_id,
        github=github,
        base_branch="main",
    ).run()
    assert delivered.delivered and not delivered.created and delivered.pr_number == 77
    assert github.created() == [] and ("add_labels", (418, ("factory:review",))) not in github.calls
    task = trace_writer.get_task(TASK_ID)
    assert task is not None and task["pr_number"] == 77


async def test_missing_remote_blocks_after_retries(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    outcome, wt, (deps, recorder) = await accepted_pipeline(trace_writer, product, tmp_path)
    github = FakePulls()
    delivered = await DeliverPhase(
        deps,
        task_id=TASK_ID,
        worktree=wt,
        spec_rel=SPEC_REL,
        run_id=outcome.run_id,
        github=github,
        base_branch="main",
        token="t0k",
    ).run()
    assert delivered.blocked_reason is BlockedReason.TIMEOUT and not delivered.pushed
    assert recorder.sleeps == [5, 20, 60] and github.calls == []
    errors = trace_writer.db.fetchall(
        "SELECT payload_json FROM events WHERE kind = 'error' AND payload_json LIKE '%push%'"
    )
    assert len(errors) >= 4 and all("t0k" not in e["payload_json"] for e in errors)
    assert (
        "git push origin agent/418" in errors[0]["payload_json"]
        or "push origin" in errors[0]["payload_json"]
    )
    task = trace_writer.get_task(TASK_ID)
    assert task is not None and task["status"] == "blocked"


async def test_merge_observer_closes_the_task(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    add_remote(product, tmp_path)
    outcome, wt, (deps, _) = await accepted_pipeline(trace_writer, product, tmp_path)
    await deliver(
        deps,
        task_id=TASK_ID,
        worktree=wt,
        spec_rel=SPEC_REL,
        outcome=outcome,
        github=FakePulls(),
        base_branch="main",
    )
    observer = MergeObserver(trace_writer)
    unknown = merged_event(999, branch="agent/999")
    assert await observer.on_merged(unknown) is False  # unknown PR and branch
    assert await observer.on_merged(merged_event(431, merged=False)) is False  # closed unmerged
    task = trace_writer.get_task(TASK_ID)
    assert task is not None and task["status"] == "pr_open"

    async def never(event: NormalisedEvent) -> None:
        raise AssertionError("not routed here")

    router = Router(trace_writer, Handlers(admit=never, cancel=never, merged=observer.on_merged))
    assert await router.handle(merged_event(431)) == "merged"
    task = trace_writer.get_task(TASK_ID)
    assert task is not None and task["status"] == "merged"
    assert trace_writer.open_pull_requests() == []
    assert await observer.on_merged(merged_event(431)) is True  # idempotent
    merged_again = await DeliverPhase(
        deps,
        task_id=TASK_ID,
        worktree=wt,
        spec_rel=SPEC_REL,
        run_id=outcome.run_id,
        github=FakePulls(),
        base_branch="main",
    ).run()
    assert merged_again.status == "merged" and not merged_again.pushed
