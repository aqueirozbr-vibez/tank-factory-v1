"""Unit 52: the PRD phase 4 gate, recorded offline.

The three conditions: eval pass rate at least 60 overall on the pilot,
reported per class (the fixture set with the fake harness); cost per
merged PR at most twice the rolling median (three issues label → PR →
merge through the composition root, read from the KPI materialiser; the
two-week window is the pilot's clock); a second installation installed
with the fake harness replays its own set and lands within 10 points of
the pilot's rate through the operator-entered reference. Each test prints
one ``GATE-EVIDENCE`` line; the tracker records them.
"""

import asyncio
import json
from pathlib import Path

import pytest

from cli.eval_cmd import load_fixture
from cli.install import InstallOptions, install
from cli.seed import TASKS, seed_repository
from orchestrator import constants
from orchestrator.eval import EvalReport, ensure_eval_set
from orchestrator.models.config import FactoryConfig
from orchestrator.trace import TraceDB, TraceWriter, verify_chain
from orchestrator.trace.kpi import KpiMaterialiser
from orchestrator.workspace.git import run_git
from tests.integration.test_eval import fast_config, installed_writer, runner_for
from tests.integration.test_phase2_gate import _gate, labeled, merged
from tests.unit.test_install import Commands, app_key

GATE_ISSUES = TASKS[:3]
CHEAP_CAPS = {"planner": 0.20, "builder": 0.40, "reviewer": 0.20}
SECOND_CONCURRENCY = 10  # N x 3 x budget is the day's cap; a 24-item replay needs 13 or so


def cheap_caps(repo: Path) -> None:
    """The phase caps an operator sets for a repository whose tasks cost cents.

    The dry run derives the per-task budget as twice the highest build-slice
    cost, floored at the industry per-task cap of 5.00 since unit 67 (2 x
    0.38 = 0.76 here, so the floor applies). Before the floor the shipped
    caps (1.50 / 3.00 / 1.50) made the budget check refuse the first phase of
    every task; the cheap caps stay so a full task (plan 0.12, build up to
    0.40, review 0.09) keeps the day's cap and the eval ceiling small for a
    repository whose tasks cost cents. The caps are roster configuration.
    """
    roster = repo / ".factory" / "roster.yaml"
    text = roster.read_text(encoding="utf-8")
    text = text.replace("phase_cap_usd: 1.50", f"phase_cap_usd: {CHEAP_CAPS['planner']:.2f}")
    text = text.replace("phase_cap_usd: 3.00", f"phase_cap_usd: {CHEAP_CAPS['builder']:.2f}")
    roster.write_text(text, encoding="utf-8", newline="\n")
    settings = repo / ".factory" / "factory.yaml"
    settings_text = settings.read_text(encoding="utf-8")
    assert "concurrency_n: 3" in settings_text
    settings.write_text(
        settings_text.replace("concurrency_n: 3", f"concurrency_n: {SECOND_CONCURRENCY}"),
        encoding="utf-8",
        newline="\n",
    )
    run_git(["add", "-A"], cwd=repo)
    run_git(
        ["commit", "-q", "-m", "chore(factory): phase caps and N for a cheap repository"],
        cwd=repo,
    )


def evidence(name: str, **facts: object) -> None:
    print(f"GATE-EVIDENCE {name}: {json.dumps(facts, sort_keys=True)}")


async def replay(
    scratch: Path,
    data_dir: Path,
    writer: TraceWriter,
    config: FactoryConfig,
    *,
    name: str,
    repo: str,
    reference: float | None = None,
) -> EvalReport:
    _fixture, items = load_fixture()
    set_id = ensure_eval_set(
        writer,
        name=name,
        repo=repo,
        items=[(i.issue, i.eval_class, None) for i in items],
        reference_pass_rate=reference,
    )
    titles = {i.issue: i.task.name for i in items}
    runner = runner_for(scratch, data_dir, writer, config, items)
    return await runner.run(set_id, trigger="manual", titles=titles)


@pytest.fixture(scope="module")
def pilot(tmp_path_factory: pytest.TempPathFactory) -> Path:
    target = tmp_path_factory.mktemp("phase4") / "demo"
    seed_repository(target)
    return target


@pytest.fixture(scope="module")
def pilot_report(pilot: Path, tmp_path_factory: pytest.TempPathFactory) -> EvalReport:
    """Condition 1's replay, shared with condition 3 as the reference."""
    data_dir = tmp_path_factory.mktemp("pilot-data")
    config = fast_config(pilot)
    db, writer = installed_writer(data_dir, config)
    try:
        return asyncio.run(
            replay(pilot, data_dir, writer, config, name="pilot-fixture", repo="demo")
        )
    finally:
        db.close()


def test_condition_1_pilot_eval_pass_rate_at_least_sixty_per_class(
    pilot_report: EvalReport,
) -> None:
    report = pilot_report
    assert report.pass_rate >= 60.0 and len(report.results) >= 20
    assert set(report.per_class) == {"bug", "docs", "feature", "test"}
    assert report.reference_pass_rate is None and report.gap_points is None
    evidence(
        "phase4.eval_pass_rate",
        overall=report.pass_rate,
        per_class=report.per_class,
        items=len(report.results),
        passed=report.passed_count,
    )


async def test_condition_2_cost_per_merged_pr_within_twice_the_median(tmp_path: Path) -> None:
    scratch = tmp_path / "demo"
    seed_repository(scratch)
    bare = tmp_path / "remote.git"  # the deliver phase pushes agent/<n> to origin
    run_git(["init", "--bare", "-q", str(bare)], cwd=tmp_path)
    run_git(["remote", "add", "origin", str(bare)], cwd=scratch)
    service, _github, db = await _gate(scratch, tmp_path / "data")
    try:
        assert service.start() == []
        for task in GATE_ISSUES:
            service.inbox.put_nowait(labeled(task.issue))
        await asyncio.wait_for(service.drain(), timeout=600)
        rows = {int(r["issue_number"]): r for r in db.fetchall("SELECT * FROM tasks")}
        statuses = {
            t.issue: (rows[t.issue]["status"], rows[t.issue]["blocked_reason"]) for t in GATE_ISSUES
        }
        assert all(s == ("pr_open", None) for s in statuses.values()), statuses
        for task in GATE_ISSUES:
            pr = int(rows[task.issue]["pr_number"])
            service.inbox.put_nowait(merged(pr, f"agent/{task.issue}"))
        await asyncio.wait_for(service.drain(), timeout=60)
        assert db.scalar("SELECT COUNT(*) FROM tasks WHERE status = 'merged'") == 3

        metrics = {m.name: m for m in KpiMaterialiser(service.writer).compute()}
        cost = metrics["cost_per_merged_pr"]
        median = metrics["cost_per_merged_pr_median_30d"]
        assert cost.value is not None and median.value is not None and median.value > 0
        assert cost.denominator == 3.0 and cost.value <= 2 * median.value
        assert metrics["over_budget_count"].value == 0.0
        day_rows = service.writer.kpis_for_day  # the merge hook materialised the day already
        assert any(
            day_rows(d) for d in {str(r["day"]) for r in db.fetchall("SELECT day FROM kpi_daily")}
        )
        evidence(
            "phase4.cost_per_merged_pr",
            cost_usd=cost.value,
            median_30d_usd=median.value,
            ratio=round(cost.value / median.value, 3),
            merged=3,
            over_budget=0,
        )
        assert verify_chain(db).ok
    finally:
        db.close()


def test_condition_3_second_installation_within_ten_points(
    pilot_report: EvalReport,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    second = tmp_path / "second"
    seed_repository(second)
    cheap_caps(second)
    monkeypatch.setenv("FACTORY_GITHUB_APP_ID", "12345")
    monkeypatch.setenv("FACTORY_GITHUB_INSTALLATION_ID", "78")
    monkeypatch.setenv("FACTORY_GITHUB_PRIVATE_KEY_FILE", str(app_key(tmp_path)))
    monkeypatch.delenv("FACTORY_PROXY_MASTER_KEY", raising=False)
    etc, data = tmp_path / "etc", tmp_path / "data"
    opts = InstallOptions(
        repo_clone=second,
        org="acme",
        repo="demo2",
        harness="fake",
        scripts_dir=data / "repo" / ".factory-dev" / "scripts",
        etc_dir=etc,
        data_dir=data,
        issues=(6, 7, 8),
        skip_image=True,
        skip_protection=True,
        vendor_env={"ANTHROPIC_API_KEY": "sk-ant-test", "OPENAI_API_KEY": "sk-oa-test"},
    )
    commands = Commands()
    code = install(opts, commands=commands, probe=lambda _url: True)
    captured = capsys.readouterr()
    assert code == 0, captured.out + captured.err
    assert (etc / "export.key").is_file() and (etc / "factory.env").is_file()
    assert "FACTORY_EXPORT_KEY_FILE=" in (etc / "factory.env").read_text(encoding="utf-8")

    clone = data / "repo"
    db = TraceDB.open(data / "trace.db")
    try:
        writer = TraceWriter(db)
        installation = writer.get_installation()
        assert installation is not None and installation["repo"] == "demo2"
        assert installation["budget_mode"] == "install"
        assert int(installation["concurrency_n"]) == SECOND_CONCURRENCY
        config = fast_config(clone)
        writer.load_config_version(**config.config_version_row())
        report = asyncio.run(
            replay(
                clone,
                data,
                writer,
                config,
                name="demo2-fixture",
                repo="demo2",
                reference=pilot_report.pass_rate,
            )
        )
        assert report.reference_pass_rate == pilot_report.pass_rate
        assert report.gap_points is not None and report.gap_points >= -constants.EVAL_GAP_POINTS
        found = writer.find_eval_set("demo2-fixture")
        assert found is not None and found["reference_pass_rate"] == pilot_report.pass_rate
        evidence(
            "phase4.second_installation",
            pilot_pass_rate=pilot_report.pass_rate,
            second_pass_rate=report.pass_rate,
            gap_points=report.gap_points,
            per_class=report.per_class,
            install_commands=len(commands.calls),
            phase_caps=CHEAP_CAPS,
            concurrency_n=SECOND_CONCURRENCY,
            dry_run_budget_usd=float(installation["per_task_budget_usd"]),
        )
        assert verify_chain(db).ok
    finally:
        db.close()
