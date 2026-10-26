"""Unit 15: the phase 1 CLI on a seeded scratch repository (fake harness, real quality commands)."""

from pathlib import Path

import pytest
import yaml

from cli.main import main
from cli.seed import TASKS, seed_repository
from orchestrator.budget.dry_run import InstallLog
from orchestrator.trace import TraceDB, TraceWriter
from orchestrator.workspace.git import run_git


@pytest.fixture(scope="module")
def seeded(tmp_path_factory: pytest.TempPathFactory) -> Path:
    target = tmp_path_factory.mktemp("scratch") / "demo"
    seed_repository(target, with_violations=True)
    return target


def test_seed_layout(seeded: Path) -> None:
    assert len(list((seeded / "specs").glob("*.md"))) == 10
    assert len(list((seeded / ".factory-dev" / "scripts").glob("*.json"))) == 20
    assert (seeded / ".factory-dev" / "known_failing" / "broken.py").is_file()
    quality = yaml.safe_load((seeded / ".factory" / "quality.yaml").read_text(encoding="utf-8"))
    assert quality["lint"][:3] == ["uv", "run", "--project"]
    log = run_git(["log", "--oneline"], cwd=seeded).stdout.strip().splitlines()
    assert len(log) == 1 and "seed" in log[0]
    assert run_git(["status", "--porcelain"], cwd=seeded).stdout.strip() == ""
    spec = (seeded / "specs" / "1-slugify.md").read_text(encoding="utf-8")
    for heading in (
        "## Outcome",
        "## Scope",
        "## Non-goals",
        "## Acceptance criteria",
        "## Constraints",
        "## Open questions",
        "## Test plan",
    ):
        assert heading in spec
    assert "WHEN" in spec and "THE SYSTEM SHALL" in spec
    assert [t.issue for t in TASKS] == list(range(1, 11))


def test_seed_refuses_non_empty_target(tmp_path: Path) -> None:
    (tmp_path / "x").write_text("x", encoding="utf-8")
    with pytest.raises(FileExistsError):
        seed_repository(tmp_path)


def test_run_spec_green_then_trace_and_verify(
    seeded: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = seeded / ".factory-dev" / "data"
    code = main(
        [
            "run-spec",
            "--repo",
            str(seeded),
            "--issue",
            "1",
            "--harness",
            "fake",
            "--skip-self-test",
            "--data-dir",
            str(data_dir),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0, out
    assert "pass · commit" in out and "cost $0.31" in out
    log = run_git(["log", "agent/1", "-1", "--format=%s %an"], cwd=seeded).stdout.strip()
    assert log == "feat(demo): slugify (#1) factory"

    assert main(["trace", "--task", "dev/demo#1", "--data-dir", str(data_dir)]) == 0
    trace_out = capsys.readouterr().out
    assert "tool_call" in trace_out and "gate_pass" in trace_out
    assert "4 tool calls" in trace_out and "cost $0.31" in trace_out
    assert "status running" in trace_out

    assert main(["verify", "--data-dir", str(data_dir)]) == 0
    assert "chain verified" in capsys.readouterr().out

    assert main(["trace", "--task", "dev/demo#999", "--data-dir", str(data_dir)]) == 1


def test_run_spec_violation_blocks(seeded: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data_dir = seeded / ".factory-dev" / "data"
    code = main(
        [
            "run-spec",
            "--repo",
            str(seeded),
            "--issue",
            "2",
            "--harness",
            "fake",
            "--skip-self-test",
            "--data-dir",
            str(data_dir),
            "--scripts",
            str(seeded / ".factory-dev" / "scripts" / "2-violation.json"),
        ]
    )
    out = capsys.readouterr().out
    assert code == 1, out
    assert "blocked (protected_path)" in out
    holdout = (
        seeded
        / ".factory-dev"
        / "data"
        / "worktrees"
        / "agent-2"
        / "tests"
        / "acceptance"
        / "test_holdout.py"
    )
    assert holdout.read_text(encoding="utf-8").endswith("assert True\n")


def test_cli_help_and_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert "run-spec" in capsys.readouterr().out
    assert main(["--version"]) == 0


def test_dry_run_derives_the_budget(  # issues 1 and 2 belong to the run-spec tests above
    seeded: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = tmp_path / "dry"
    code = main(
        [
            "dry-run",
            "--repo",
            str(seeded),
            "--issues",
            "3",
            "4",
            "5",
            "--data-dir",
            str(data_dir),
            "--harness",
            "fake",
            "--skip-self-test",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0, out
    assert (
        "per-task budget $5.00" in out
    )  # the floor; 2x the highest seeded cost (issue 5: 0.35) sits below
    db = TraceDB.open(data_dir / "trace.db")
    try:
        installation = TraceWriter(db).get_installation()
        assert installation is not None
        assert (
            installation["per_task_budget_usd"] == 5.0
        )  # the floor; 2x the highest cost 0.35 sits below it
        assert installation["budget_mode"] == "install"
        assert installation["daily_cap_usd"] == pytest.approx(3 * 3 * 5.0)
    finally:
        db.close()
    records = InstallLog(data_dir).records()
    assert len(records) == 1 and records[0]["per_task_budget_usd"] == 5.0
    assert records[0]["floor_usd"] == 5.0 and records[0]["highest_cost_usd"] == 0.35
    assert records[0]["completed"] == 3 and records[0]["kind"] == "dry_run"
    assert main(["dry-run", "--repo", str(seeded), "--issues", "3", "4"]) == 2
