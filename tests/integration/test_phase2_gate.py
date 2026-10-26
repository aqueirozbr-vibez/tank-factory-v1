"""Unit 32: the PRD phase 2 gate, offline, through the real composition root.

Ten issues go label → PR with no human event; eight merge; the ambiguous
issue stops with exactly one question. The fake harness replays the seed
scripts; the quality gates are the real ruff, mypy and pytest.
"""

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from cli.seed import ALL_TASKS, AMBIGUOUS, TASKS, issue_body, seed_repository
from orchestrator.config import load_factory_config
from orchestrator.github.client import IssueComment
from orchestrator.harness.fake import KeyedFakeHarness
from orchestrator.models.enums import AgentRole
from orchestrator.models.events import NormalisedEvent
from orchestrator.models.settings import Settings
from orchestrator.service import FactoryService
from orchestrator.trace import TraceDB, TraceWriter, verify_chain
from orchestrator.workspace.git import run_git
from orchestrator.workspace.holdout import HoldoutCheckout

AT = "2026-10-24T12:00:00.000+00:00"
AGENTS = {"orchestrator", "planner", "builder", "reviewer"}


class GateGitHub:
    """Issues, comments, labels and pull requests for many issues; one write-access user."""

    def __init__(self) -> None:
        self.comments: dict[int, list[str]] = {}
        self.labels: dict[int, list[str]] = {}
        self.pulls: list[dict[str, Any]] = []
        self.next_pull = 100

    async def collaborator_permission(self, login: str, *, task_id: str) -> str:
        return "write" if login == "rsouza" else "none"

    async def get_issue(self, number: int, *, task_id: str) -> dict[str, Any]:
        task = next(t for t in ALL_TASKS if t.issue == number)
        return {
            "number": number,
            "title": task.name,  # the spec path derives from the title: specs/<n>-<name>.md
            "body": issue_body(task),
            "user": {"login": "rsouza"},
            "author_association": "MEMBER",
            "labels": [{"name": label} for label in self.labels.get(number, [])],
        }

    async def list_issue_comments(self, number: int, *, task_id: str) -> list[IssueComment]:
        return []

    async def create_comment(self, number: int, body: str, *, task_id: str) -> int:
        self.comments.setdefault(number, []).append(body)
        return sum(len(v) for v in self.comments.values())

    async def add_labels(self, number: int, labels: Sequence[str], *, task_id: str) -> None:
        self.labels.setdefault(number, []).extend(labels)

    async def remove_label(self, number: int, label: str, *, task_id: str) -> bool:
        current = self.labels.setdefault(number, [])
        if label in current:
            current.remove(label)
            return True
        return False

    async def list_labels(self, number: int, *, task_id: str) -> list[str]:
        return list(self.labels.get(number, []))

    async def get_branch_sha(self, branch: str, *, task_id: str) -> str | None:
        return None

    async def list_pulls_for_branch(self, head: str, *, task_id: str) -> list[dict[str, Any]]:
        return [p for p in self.pulls if p["head"]["ref"] == head]

    async def create_pull(
        self, *, title: str, head: str, base: str, body: str, task_id: str
    ) -> dict[str, Any]:
        self.next_pull += 1
        pull = {
            "number": self.next_pull,
            "state": "open",
            "title": title,
            "head": {"ref": head, "sha": "0" * 40},
            "base": {"ref": base},
            "html_url": f"https://github.com/acme/demo/pull/{self.next_pull}",
            "body": body,
        }
        self.pulls.append(pull)
        return pull

    async def request_reviewers(
        self, number: int, reviewers: Sequence[str], *, task_id: str, teams: Sequence[str] = ()
    ) -> None:
        return None


def labeled(issue: int) -> NormalisedEvent:
    return NormalisedEvent(
        kind="issue_labeled",
        source="webhook",
        github_event_id=f"ready-{issue}",
        event_type="issues",
        action="labeled",
        issue_number=issue,
        actor_login="rsouza",
        label="factory:ready",
        received_at=AT,
    )


def merged(pr: int, branch: str) -> NormalisedEvent:
    return NormalisedEvent(
        kind="pull_request_merged",
        source="webhook",
        github_event_id=f"merged-{pr}",
        event_type="pull_request",
        action="closed",
        pr_number=pr,
        branch=branch,
        head_sha="0" * 40,
        actor_login="rsouza",
        received_at=AT,
    )


@pytest.fixture(scope="module")
def scratch(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("phase2")
    target = root / "demo"
    seed_repository(target)
    bare = root / "remote.git"
    run_git(["init", "--bare", "-q", str(bare)], cwd=root)
    run_git(["remote", "add", "origin", str(bare)], cwd=target)
    return target


async def _gate(scratch: Path, data_dir: Path) -> tuple[FactoryService, GateGitHub, TraceDB]:
    settings = Settings(data_dir=data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    db = TraceDB.open(settings.trace_db_path)
    writer = TraceWriter(db)
    config = load_factory_config(scratch / ".factory")
    writer.load_config_version(**config.config_version_row())
    writer.insert_installation(
        org="acme",
        repo="demo",
        factory_version="gate",
        config_sha=config.config_sha,
        per_task_budget_usd=6.0,
        export_pubkey="pk",
        concurrency_n=1,
    )
    github = GateGitHub()
    keyed = KeyedFakeHarness.from_seed_dir(scratch / ".factory-dev" / "scripts")
    holdout = HoldoutCheckout(str(scratch), settings.holdout_dir)
    holdout.ensure()
    service = FactoryService(
        settings=settings,
        writer=writer,
        config=config,
        github=github,
        repo_clone=scratch,
        harnesses=dict.fromkeys(AgentRole, keyed),
        base_branch="main",
        holdout=holdout,
        token_for=None,
    )
    return service, github, db


async def test_phase_2_gate_offline(scratch: Path, tmp_path: Path) -> None:
    service, github, db = await _gate(scratch, tmp_path / "data")
    try:
        assert service.start() == []
        for task in ALL_TASKS:
            service.inbox.put_nowait(labeled(task.issue))
        await asyncio.wait_for(service.drain(), timeout=900)

        writer = service.writer
        rows = {int(r["issue_number"]): r for r in db.fetchall("SELECT * FROM tasks")}
        assert len(rows) == 11
        for task in TASKS:
            row = rows[task.issue]
            assert row["status"] == "pr_open", (task.issue, row["status"], row["blocked_reason"])
            assert row["risk"] == "risk:low" and row["pr_number"] is not None
            # only the factory's own queue comment; no question, block or approval asked
            assert all(c.startswith("Admitted.") for c in github.comments.get(task.issue, []))
            assert "factory:review" in github.labels[task.issue]
            assert "factory:needs-human" not in github.labels[task.issue]
        assert len(github.pulls) == 10

        ambiguous = rows[AMBIGUOUS.issue]
        assert ambiguous["status"] == "blocked" and ambiguous["blocked_reason"] == "open_questions"
        question_comments = [
            c for c in github.comments[AMBIGUOUS.issue] if not c.startswith("Admitted.")
        ]
        assert len(question_comments) == 1 and "half" in question_comments[0]
        assert question_comments[0].count("\n1. ") == 1  # exactly one question
        assert "\n2. " not in question_comments[0]
        assert "factory:needs-human" in github.labels[AMBIGUOUS.issue]

        human_events = db.fetchall(
            "SELECT actor, kind FROM events WHERE kind IN ('control', 'approval', 'audit')"
        )
        assert [e["actor"] for e in human_events if e["actor"] not in AGENTS] == []
        assert (scratch / ".factory" / "risk.yaml").is_file()
        assert (scratch / ".factory" / "review-policy.yaml").is_file()

        for task in TASKS[:8]:
            pr = int(rows[task.issue]["pr_number"])
            service.inbox.put_nowait(merged(pr, f"agent/{task.issue}"))
        await asyncio.wait_for(service.drain(), timeout=60)
        statuses = [
            r["status"] for r in db.fetchall("SELECT status FROM tasks ORDER BY issue_number")
        ]
        assert statuses.count("merged") == 8 and statuses.count("pr_open") == 2
        assert writer.open_pull_requests().__len__() == 2

        runs = db.fetchall("SELECT status FROM runs")
        assert [r["status"] for r in runs].count("success") == 10
        assert db.scalar("SELECT COUNT(*) FROM gate_results WHERE passed = 0") == 0
        assert verify_chain(db).ok
    finally:
        db.close()
