"""Unit 24: the risk classification instrument and its plan-phase integration."""

from pathlib import Path

import pytest
import yaml

from orchestrator.errors import ConfigError
from orchestrator.gates.spec import parse_spec
from orchestrator.instruments.risk import (
    RiskInstrument,
    classify,
    infer_tiers,
    override,
    scope_paths,
    write_risk_header,
)
from orchestrator.models.enums import BlockedReason, RiskClass
from orchestrator.models.envelopes import RiskInputs, Tier
from orchestrator.phases.plan import PlanOutcome
from orchestrator.trace import TraceWriter, verify_chain
from orchestrator.workspace.git import run_git
from tests.unit.test_spec_gate import SPEC_REL, SPEC_TEXT, TASK, run_plan, script

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "fixtures" / "factory_config"
RISK_YAML = (CONFIG_DIR / "risk.yaml").read_text(encoding="utf-8")
INVOICE = "src/billing/invoice.py"
GHOST_SCOPE = SPEC_TEXT.replace(
    "- src/billing/invoice.py\n- tests/unit/test_invoice_due_date.py\n", "- nowhere/missing.py\n"
)


@pytest.fixture(scope="module")
def instrument() -> RiskInstrument:
    return RiskInstrument.from_yaml(RISK_YAML)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    for rel in (INVOICE, "src/api/invoices.py", "src/web/x.py", "docs/guide.md", ".factory/r.yaml"):
        target = tmp_path.joinpath(*rel.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x\n", encoding="utf-8")
    return tmp_path


def inputs(
    tiers: list[Tier], lines: int, modules: int, deps: list[str] | None = None
) -> RiskInputs:
    return RiskInputs(
        tiers_touched=tiers,
        est_lines=lines,
        new_dependencies=deps or [],
        modules=[f"m{i}" for i in range(modules)],
    )


# --- the instrument ---------------------------------------------------------------


def test_instrument_loads_and_rejects_bad_yaml(instrument: RiskInstrument) -> None:
    assert RiskInstrument.load(CONFIG_DIR) == instrument
    assert instrument.tiers["A"].base == 100 and instrument.tiers["U"].base == 0
    assert instrument.size.apply(100) == (0, 1.0) and instrument.size.apply(300) == (1, 1.3)
    assert instrument.blast_radius.apply(4) == (2, 1.5)
    with pytest.raises(ConfigError, match="invalid"):
        RiskInstrument.from_yaml("version: [")
    raw = yaml.safe_load(RISK_YAML)
    del raw["tiers"]["U"]
    with pytest.raises(ConfigError, match="lacks tiers"):
        RiskInstrument.from_yaml(yaml.safe_dump(raw))
    with pytest.raises(ConfigError, match="missing"):
        RiskInstrument.load(CONFIG_DIR / "nope")


@pytest.mark.parametrize(
    ("tiers", "lines", "modules", "deps", "score", "expected"),
    [
        (["C"], 60, 1, None, 40.0, RiskClass.LOW),  # retry on one webhook handler
        (["B"], 300, 3, None, 109.2, RiskClass.HIGH),  # additive migration plus API
        (["D"], 40, 5, None, 30.0, RiskClass.LOW),  # UI copy across five screens
        (["C"], 60, 1, ["tenacity"], 60.0, RiskClass.HIGH),  # new package for the retry
    ],
)
def test_prd_worked_examples(
    instrument: RiskInstrument,
    repo: Path,
    tiers: list[Tier],
    lines: int,
    modules: int,
    deps: list[str] | None,
    score: float,
    expected: RiskClass,
) -> None:
    breakdown = classify(
        instrument, scope=(INVOICE,), inputs=inputs(tiers, lines, modules, deps), worktree=repo
    )
    assert (breakdown.score, breakdown.risk_class) == (score, expected)
    assert breakdown.equation().endswith(f"= {score:g}")
    assert breakdown.payload()["risk"] == str(expected)


def test_tiers_come_from_paths_and_the_planner(instrument: RiskInstrument, repo: Path) -> None:
    assert infer_tiers(instrument, [".factory/r.yaml"]) == {"A": ".factory/r.yaml"}
    assert infer_tiers(instrument, ["docs/guide.md", "README.md"]) == {"E": "docs/guide.md"}
    assert infer_tiers(instrument, [INVOICE]) == {}  # PRD example 5: billing is a human call
    governed = classify(
        instrument,
        scope=(".factory/r.yaml", "src/web/x.py"),
        inputs=inputs(["D"], 10, 1),
        worktree=repo,
    )
    assert governed.tier == "A" and governed.risk_class is RiskClass.GOVERNED
    assert governed.score == 100.0 and "CODEOWNER" in governed.conclusion()
    docs = classify(instrument, scope=("docs/guide.md",), inputs=inputs([], 20, 1), worktree=repo)
    assert docs.tier == "E" and docs.score == 10.0 and docs.risk_class is RiskClass.LOW
    mixed = classify(
        instrument, scope=("src/web/x.py",), inputs=inputs(["D", "B"], 20, 1), worktree=repo
    )
    assert mixed.tier == "B" and mixed.score == 70.0 and mixed.risk_class is RiskClass.HIGH


def test_unclassifiable_cases(instrument: RiskInstrument, repo: Path) -> None:
    none = classify(instrument, scope=(), inputs=inputs([], 10, 1), worktree=repo)
    assert none.unclassifiable and none.tier == "U" and none.score == 0.0
    assert "Scope names no paths" in none.reasons and none.payload()["risk"] is None
    ghost = classify(
        instrument, scope=("nope/missing.py",), inputs=inputs(["C"], 10, 1), worktree=repo
    )
    assert ghost.unclassifiable and "no Scope path exists in the repository" in ghost.reasons
    undeclared = classify(
        instrument, scope=("src/web/x.py",), inputs=inputs([], 10, 1), worktree=repo
    )
    assert undeclared.unclassifiable and "no tier declared or inferred" in undeclared.reasons
    new_file = classify(
        instrument, scope=("src/billing/new_module.py",), inputs=inputs(["C"], 10, 1), worktree=repo
    )
    assert new_file.risk_class is RiskClass.LOW  # the directory exists, so a new file counts
    assert "tier U" in none.correction_text() and "nope/missing.py" in ghost.correction_text()


def test_override_header_and_scope_parsing(instrument: RiskInstrument, repo: Path) -> None:
    low = classify(instrument, scope=("src/web/x.py",), inputs=inputs(["C"], 60, 1), worktree=repo)
    forced = override(low, RiskClass.HIGH, by="mlima", reason="sits on the billing path")
    assert forced.risk_class is RiskClass.HIGH and forced.score == 40.0
    assert forced.overridden_by == "mlima"
    assert forced.reasons[-1] == "override by mlima: sits on the billing path"
    assert forced.payload()["override_reason"] == "sits on the billing path"

    placeholder = "# T\n\nrisk: (left blank; code fills the class)\n\n## Outcome\n"
    assert (
        write_risk_header(placeholder, RiskClass.HIGH) == "# T\n\nrisk: risk:high\n\n## Outcome\n"
    )
    assert write_risk_header("# T\n\n## Outcome\n", RiskClass.LOW) == (
        "# T\n\nrisk: risk:low\n\n## Outcome\n"
    )

    assert scope_paths(parse_spec(SPEC_TEXT)) == (INVOICE, "tests/unit/test_invoice_due_date.py")
    mixed = "## Scope\n\n- `src/a.py` (read only)\n- the billing module\n- src/a.py\n"
    assert scope_paths(parse_spec(mixed)) == ("src/a.py",)


# --- the plan phase with the instrument -----------------------------------------------


async def test_plan_phase_classifies_commits_and_labels(
    trace_writer: TraceWriter, product: Path, tmp_path: Path
) -> None:
    outcome, github, _ = await run_plan(
        trace_writer,
        product,
        tmp_path,
        [script(SPEC_TEXT)],
        instrument=RiskInstrument.from_yaml(RISK_YAML),
    )
    assert isinstance(outcome, PlanOutcome) and outcome.written and outcome.risk is not None
    assert outcome.risk.risk_class is RiskClass.LOW and outcome.risk.score == 40.0
    task = trace_writer.get_task(TASK)
    assert task is not None and task["risk"] == "risk:low" and task["risk_score"] == 40.0
    assert task["spec_sha"] == outcome.spec_sha and len(task["spec_sha"]) == 40
    phases = [
        (p["name"], p["status"])
        for p in trace_writer.db.fetchall("SELECT name, status FROM phases ORDER BY seq")
    ]
    assert phases == [("plan", "pass"), ("spec_gate", "pass"), ("risk", "pass"), ("commit", "pass")]
    wt = tmp_path / "w" / "agent-418"
    spec_lines = (wt / "specs" / "418-invoice-due-date.md").read_text(encoding="utf-8").splitlines()
    assert spec_lines[2] == "risk: risk:low"
    assert run_git(["status", "--porcelain"], cwd=wt).stdout.strip() == ""
    subject = run_git(["log", "-1", "--format=%s"], cwd=wt).stdout.strip()
    assert subject == f"spec: {SPEC_REL} (risk:low)"
    assert run_git(["rev-parse", f"HEAD:{SPEC_REL}"], cwd=wt).stdout.strip() == outcome.spec_sha
    assert github.calls == [("add_labels", (418, ("risk:low",)))]
    assert verify_chain(trace_writer.db).ok


async def test_unclassifiable_spec_gets_one_correction(
    trace_writer: TraceWriter, product: Path, tmp_path: Path
) -> None:
    outcome, github, _ = await run_plan(
        trace_writer,
        product,
        tmp_path,
        [script(GHOST_SCOPE), script(SPEC_TEXT)],
        instrument=RiskInstrument.from_yaml(RISK_YAML),
    )
    assert isinstance(outcome, PlanOutcome) and outcome.written
    assert outcome.planner_calls == 2 and outcome.corrections == 1
    gate_rows = trace_writer.db.fetchall(
        "SELECT status FROM phases WHERE name = 'spec_gate' ORDER BY seq"
    )
    assert [r["status"] for r in gate_rows] == ["fail", "pass"]
    assert github.calls == [("add_labels", (418, ("risk:low",)))]


async def test_exhausted_unclassifiable_blocks_spec_gate(
    trace_writer: TraceWriter, product: Path, tmp_path: Path
) -> None:
    outcome, github, _ = await run_plan(
        trace_writer,
        product,
        tmp_path,
        [script(GHOST_SCOPE)],
        instrument=RiskInstrument.from_yaml(RISK_YAML),
    )
    assert isinstance(outcome, PlanOutcome)
    assert outcome.blocked_reason is BlockedReason.SPEC_GATE and outcome.planner_calls == 3
    assert outcome.corrections == 2 and "tier U" in outcome.evidence and github.calls == []
    task = trace_writer.get_task(TASK)
    assert task is not None and task["risk"] is None and task["spec_sha"] == ""
