"""Unit 47: the eval runner on the fixture set with the fake harness; a 13-point drop blocks."""

import json
from collections import Counter
from pathlib import Path

import pytest

from cli.eval_cmd import (
    FIXTURE_SET,
    FixtureItem,
    fixture_harness,
    fixture_issue_source,
    load_fixture,
)
from cli.seed import seed_repository
from orchestrator import constants
from orchestrator.config import load_factory_config
from orchestrator.eval import (
    EvalError,
    EvalItem,
    EvalRunner,
    ItemResult,
    delta_points,
    ensure_eval_set,
    pass_rates,
)
from orchestrator.models.config import FactoryConfig, QualityCommands
from orchestrator.models.enums import AgentRole
from orchestrator.models.settings import Settings
from orchestrator.trace import TraceDB, TraceWriter
from orchestrator.workspace.git import run_git
from orchestrator.workspace.holdout import HoldoutCheckout
from tests.unit.test_build_phase import OK


def item(issue: int, eval_class: str) -> EvalItem:
    return EvalItem(f"i{issue}", issue, eval_class, f"issue {issue}")


def result(issue: int, eval_class: str, passed: bool) -> ItemResult:
    return ItemResult(item(issue, eval_class), passed, 0.1, 1, None if passed else "x")


def test_pass_rates_and_delta() -> None:
    overall, per_class = pass_rates(
        [
            result(1, "bug", True),
            result(2, "bug", False),
            result(3, "feature", True),
            result(4, "docs", True),
        ]
    )
    assert overall == 75.0 and per_class == {"bug": 50.0, "docs": 100.0, "feature": 100.0}
    assert pass_rates([]) == (0.0, {})
    assert delta_points(75.0, None) is None and delta_points(75.0, 90.0) == -15.0


def test_fixture_set_has_the_class_mix() -> None:
    name, items = load_fixture()
    assert name == "fixture-24" and len(items) == 24
    assert len({i.issue for i in items}) == 24 and all(101 <= i.issue <= 124 for i in items)
    assert Counter(i.eval_class for i in items) == {"feature": 8, "bug": 8, "test": 4, "docs": 4}
    assert all(1 <= i.seed <= 10 for i in items)
    assert all(i.task.issue == i.issue for i in items)
    raw = json.loads(FIXTURE_SET.read_text(encoding="utf-8"))
    assert raw["repo"] == "demo" and raw["classes"] == ["bug", "test", "docs", "feature"]


@pytest.fixture(scope="module")
def scratch(tmp_path_factory: pytest.TempPathFactory) -> Path:
    target = tmp_path_factory.mktemp("eval") / "demo"
    seed_repository(target)
    return target


def fast_config(scratch: Path) -> FactoryConfig:
    config = load_factory_config(scratch / ".factory")
    quick = QualityCommands(format=OK, lint=OK, typecheck=OK, test=OK, full_suite=OK)
    return config.model_copy(update={"quality": quick})


def installed_writer(data_dir: Path, config: FactoryConfig) -> tuple[TraceDB, TraceWriter]:
    db = TraceDB.open(data_dir / "trace.db")
    writer = TraceWriter(db)
    writer.load_config_version(**config.config_version_row())
    writer.insert_installation(
        org="acme",
        repo="demo",
        factory_version="eval-test",
        config_sha=config.config_sha,
        per_task_budget_usd=6.0,
        export_pubkey="pk",
    )
    return db, writer


def runner_for(
    scratch: Path,
    data_dir: Path,
    writer: TraceWriter,
    config: FactoryConfig,
    items: list[FixtureItem],
    *,
    failing: dict[int, str] | None = None,
) -> EvalRunner:
    holdout = HoldoutCheckout(str(scratch), data_dir / "holdout")
    holdout.ensure()
    keyed = fixture_harness(items, failing=failing)
    return EvalRunner(
        writer=writer,
        settings=Settings(data_dir=data_dir),
        config=config,
        repo_clone=scratch,
        harnesses=dict.fromkeys(AgentRole, keyed),
        holdout=holdout,
        issues=fixture_issue_source(items),
        factory_version="eval-test",
    )


async def test_fixture_runs_and_a_13_point_regression_blocks(scratch: Path, tmp_path: Path) -> None:
    config = fast_config(scratch)
    data_dir = tmp_path / "data"
    db, writer = installed_writer(data_dir, config)
    try:
        name, items = load_fixture()
        set_id = ensure_eval_set(
            writer, name=name, repo="demo", items=[(i.issue, i.eval_class, None) for i in items]
        )
        assert ensure_eval_set(writer, name=name, repo="demo", items=[]) == set_id  # idempotent
        titles = {i.issue: i.task.name for i in items}

        baseline = await runner_for(scratch, data_dir, writer, config, items).run(
            set_id, trigger="manual", titles=titles
        )
        assert baseline.pass_rate == 100.0 and baseline.passed_count == 24
        assert baseline.per_class == {"bug": 100.0, "docs": 100.0, "feature": 100.0, "test": 100.0}
        assert baseline.baseline_run_id is None and baseline.delta_points is None
        assert not baseline.blocked
        assert all(r.cost_usd > 0 for r in baseline.results)
        row = writer.get_eval_run(baseline.run_id)
        assert row is not None and row["status"] == "complete" and row["pass_rate"] == 100.0
        assert row["blocked"] == 0 and json.loads(row["per_class_json"])["bug"] == 100.0
        assert len(writer.eval_results(baseline.run_id)) == 24
        assert (
            run_git(["branch", "--list", constants.EVAL_BRANCH], cwd=scratch).stdout.strip() == ""
        )
        tasks_in_installation = int(writer.db.scalar("SELECT COUNT(*) FROM tasks") or 0)
        assert tasks_in_installation == 0  # the replay's phases live in the run's scratch trace
        scratch_db = TraceDB.open(data_dir / "eval" / baseline.run_id / "trace.db")
        try:
            assert int(scratch_db.scalar("SELECT COUNT(*) FROM tasks") or 0) == 24
            assert (
                int(scratch_db.scalar("SELECT COUNT(*) FROM phases WHERE name = 'full_suite'") or 0)
                == 24
            )
        finally:
            scratch_db.close()

        failing = {
            101: "eval regression",
            105: "eval regression",
            109: "eval regression",
            121: "eval regression",
        }
        regressed = await runner_for(scratch, data_dir, writer, config, items, failing=failing).run(
            set_id, trigger="config_pr", trigger_ref="42", titles=titles
        )
        assert regressed.passed_count == 20 and regressed.pass_rate == pytest.approx(
            83.33, abs=0.01
        )
        assert regressed.baseline_run_id == baseline.run_id
        assert regressed.delta_points == pytest.approx(-16.67, abs=0.01) and regressed.blocked
        failed = {r.item.issue_number: r.failure_reason for r in regressed.results if not r.passed}
        assert set(failed) == set(failing)
        assert all(reason is not None and "eval regression" in reason for reason in failed.values())
        row = writer.get_eval_run(regressed.run_id)
        assert row is not None and row["blocked"] == 1 and row["trigger"] == "config_pr"
        assert row["trigger_ref"] == "42" and row["baseline_run_id"] == baseline.run_id
        events = writer.db.fetchall(
            "SELECT payload_json FROM events WHERE kind = 'log' AND payload_json LIKE '%eval_run%'"
        )
        assert len(events) == 2 and json.loads(events[-1]["payload_json"])["blocked"] is True
    finally:
        db.close()


async def test_small_sets_and_bad_classes_are_refused(scratch: Path, tmp_path: Path) -> None:
    config = fast_config(scratch)
    data_dir = tmp_path / "data"
    db, writer = installed_writer(data_dir, config)
    try:
        _name, items = load_fixture()
        few = items[:5]
        set_id = ensure_eval_set(
            writer, name="few", repo="demo", items=[(i.issue, i.eval_class, None) for i in few]
        )
        with pytest.raises(EvalError, match="at least 20"):
            await runner_for(scratch, data_dir, writer, config, few).run(set_id, trigger="manual")
        with pytest.raises(EvalError, match="eval class"):
            ensure_eval_set(writer, name="bad", repo="demo", items=[(1, "chore", None)])
        full = ensure_eval_set(
            writer, name="full", repo="demo", items=[(i.issue, i.eval_class, None) for i in items]
        )
        with pytest.raises(EvalError, match="baseline run"):
            await runner_for(scratch, data_dir, writer, config, items).run(
                full, trigger="manual", baseline_run_id="nope"
            )
    finally:
        db.close()
