"""Unit 16: the PRD phase 1 gate, driven by the fake harness with the real quality tools."""

import sys
from pathlib import Path

import pytest

from cli.run_spec import run_spec
from cli.seed import TASKS, seed_repository
from orchestrator.config import load_factory_config
from orchestrator.gates.selftest import self_test
from orchestrator.models.config import QualityCommands
from orchestrator.models.settings import Settings
from orchestrator.trace import TraceDB, verify_chain
from orchestrator.workspace.git import run_git

PY = (sys.executable, "-I", "-S", "-c")
FAILS = (*PY, "import sys; sys.exit(1)")
PLACEHOLDER = (*PY, "print('typecheck ok')")


@pytest.fixture(scope="module")
def green_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    target = tmp_path_factory.mktemp("gate") / "demo"
    seed_repository(target, with_violations=True)
    return target


@pytest.fixture(scope="module")
def violation_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    target = tmp_path_factory.mktemp("gate-violations") / "demo"
    seed_repository(target, with_violations=True)
    return target


def test_gate_1_ten_specs_ten_green_branches(green_repo: Path) -> None:
    data_dir = green_repo / ".factory-dev" / "data"
    results = []
    for task in TASKS:
        code = run_spec(
            repo=green_repo,
            issue=task.issue,
            harness="fake",
            data_dir=data_dir,
            skip_self_test=task.issue != 1,  # the real self-test runs once
        )
        results.append(code)
    assert results == [0] * 10

    for task in TASKS:
        log = run_git(
            ["log", f"agent/{task.issue}", "-1", "--format=%s|%an"], cwd=green_repo
        ).stdout.strip()
        assert log == f"feat(demo): {task.name} (#{task.issue})|factory"
        base = run_git(["rev-list", "--count", f"main..agent/{task.issue}"], cwd=green_repo)
        assert base.stdout.strip() == "1"

    db = TraceDB.open(data_dir / "trace.db")
    try:
        runs = db.fetchall("SELECT status, total_cost_usd FROM runs ORDER BY started_at")
        assert [r["status"] for r in runs] == ["success"] * 10
        assert all(r["total_cost_usd"] > 0 for r in runs)
        expected_cost = sum(round(0.30 + t.issue * 0.01, 2) for t in TASKS)
        assert sum(r["total_cost_usd"] for r in runs) == pytest.approx(expected_cost)
        assert db.scalar("SELECT COUNT(*) FROM events WHERE kind = 'tool_call'") == 40
        assert db.scalar("SELECT COUNT(*) FROM gate_results WHERE passed = 0") == 0
        report = verify_chain(db)
        assert report.ok and report.records > 100
    finally:
        db.close()


def test_gate_2_seeded_holdout_edit_rolled_back_ten_of_ten(violation_repo: Path) -> None:
    data_dir = violation_repo / ".factory-dev" / "data"
    outcomes = []
    for task in TASKS:
        code = run_spec(
            repo=violation_repo,
            issue=task.issue,
            harness="fake",
            data_dir=data_dir,
            scripts=violation_repo / ".factory-dev" / "scripts" / f"{task.issue}-violation.json",
            skip_self_test=True,
        )
        outcomes.append(code)
        holdout = (
            data_dir
            / "worktrees"
            / f"agent-{task.issue}"
            / "tests"
            / "acceptance"
            / "test_holdout.py"
        )
        assert holdout.read_text(encoding="utf-8").endswith("assert True\n")
    assert outcomes == [1] * 10

    db = TraceDB.open(data_dir / "trace.db")
    try:
        tasks = db.fetchall("SELECT status, blocked_reason FROM tasks")
        assert [(t["status"], t["blocked_reason"]) for t in tasks] == [
            ("blocked", "protected_path")
        ] * 10
        assert db.scalar("SELECT COUNT(*) FROM events WHERE kind = 'incident'") == 10
        assert (
            db.scalar(
                "SELECT COUNT(*) FROM gate_results"
                " WHERE gate = 'permissions_enforce' AND passed = 0"
            )
            == 10
        )
        assert db.scalar("SELECT COUNT(*) FROM runs WHERE status = 'success'") == 0
        assert verify_chain(db).ok
    finally:
        db.close()


async def test_gate_3_self_test_rejects_a_placeholder_every_time(green_repo: Path) -> None:
    env = Settings().subprocess_base_env()
    fixture = green_repo / ".factory-dev" / "known_failing"
    seeded = load_factory_config(green_repo / ".factory")
    healthy = await self_test(seeded.quality, fixture, env=env)
    assert healthy.ok, [c.note for c in healthy.checks]

    placeholder = QualityCommands(
        format=FAILS, lint=FAILS, typecheck=PLACEHOLDER, test=FAILS, full_suite=FAILS
    )
    refusals = 0
    for _ in range(20):
        report = await self_test(placeholder, fixture, env=env, timeout_s=30)
        if not report.ok and report.placeholders == ["typecheck"]:
            refusals += 1
    assert refusals == 20
