"""Unit 23: the spec gate and open questions in the plan phase."""

import shutil
from pathlib import Path

import pytest

from orchestrator.gates.spec import parse_spec, run_spec_gates, spec_ac_holdout_map, spec_ears
from orchestrator.harness.fake import FakeEffect, FakeHarness, FakeScript, FakeUsage
from orchestrator.instruments.risk import RiskInstrument
from orchestrator.intake import messages
from orchestrator.intake.issue_fetcher import TaggedIssue, parse_sections
from orchestrator.models.enums import BlockedReason, GateName, TaskStatus, WorkspaceKind
from orchestrator.models.settings import Settings
from orchestrator.phases.build import BuildDeps
from orchestrator.phases.plan import PlanPhase
from orchestrator.trace import TraceWriter, verify_chain
from orchestrator.workspace.git import run_git
from orchestrator.workspace.worktree import create_worktree
from tests.unit.test_admission import FakeGitHub
from tests.unit.test_build_phase import make_config

ROOT = Path(__file__).resolve().parents[2]
SPEC_TEXT = (ROOT / "fixtures" / "specs" / "418-invoice-due-date.md").read_text(encoding="utf-8")
SPEC_REL = "specs/418-invoice-due-date.md"
TASK = "acme/atlas-api#418"
HOLDOUT_IDS = {
    "tests/acceptance/test_invoice_due_date_holdout.py::test_due_date",
    "tests/acceptance/test_invoice_due_date_holdout.py::test_zero_terms",
}


class FakeHoldout:
    def __init__(self, ids: set[str], *, source: Path | None = None) -> None:
        self.ids = ids
        self.refreshes = 0
        self.copies = 0
        self.source = source

    @property
    def tests_dir(self) -> Path:
        return self.source or Path("holdout").resolve() / "tests" / "acceptance"

    def refresh(self) -> str:
        self.refreshes += 1
        return "f" * 40

    def has_test(self, test_id: str) -> bool:
        return test_id in self.ids

    def copy_into(self, workspace: Path) -> Path:
        self.copies += 1
        destination = workspace / "tests" / "acceptance"
        if self.source is not None:
            shutil.copytree(self.source, destination, dirs_exist_ok=True)
        return destination


def envelope(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "status": "success",
        "summary": "Spec",
        "artifacts": [SPEC_REL],
        "notes_for_next_agent": "",
        "risk_inputs": {
            "tiers_touched": ["C"],
            "est_lines": 60,
            "new_dependencies": [],
            "modules": ["billing"],
        },
        "open_questions": [],
    }
    base.update(overrides)
    return base


def script(spec_text: str, **env_overrides: object) -> FakeScript:
    return FakeScript(
        session_id="plan",
        effects=(FakeEffect(write=SPEC_REL, content=spec_text),),
        envelope=envelope(**env_overrides),
        usage=FakeUsage(cost_usd=0.2),
    )


def issue(language_body: str | None = None) -> TaggedIssue:
    body = (
        language_body
        or "## Outcome\n\nDue date.\n\n## Acceptance criteria\n\n1. WHEN x THE SYSTEM SHALL y.\n"
    )
    return TaggedIssue(
        418, "Invoice due date", "rsouza", "MEMBER", True, body, parse_sections(body), ()
    )


# --- parsing and the four gates ---------------------------------------------------


def test_parse_fixture_spec() -> None:
    spec = parse_spec(SPEC_TEXT)
    assert spec.title == "418 — Invoice due date"
    assert spec.order == (
        "Outcome",
        "Scope",
        "Non-goals",
        "Acceptance criteria",
        "Constraints",
        "Open questions",
        "Test plan",
    )
    assert [n for n, _ in spec.criteria] == [1, 2]
    assert (
        spec.criteria[0][1].startswith("WHEN an Invoice is created")
        and "plus the terms." in spec.criteria[0][1]
    )
    assert spec.holdout_ids_for(1) == (
        "tests/acceptance/test_invoice_due_date_holdout.py::test_due_date",
    )
    assert "tests/unit/test_invoice_due_date.py::test_due_date_adds_terms" in spec.plan[1]
    assert spec.open_questions == ()


def test_all_gates_pass_on_fixture(tmp_path: Path) -> None:
    outcome = run_spec_gates(SPEC_TEXT, has_test=lambda t: t in HOLDOUT_IDS, worktree=tmp_path)
    assert outcome.passed and outcome.missing_holdout == () and outcome.first_failure is None
    assert [r.gate for r in outcome.results] == [
        GateName.SPEC_SECTIONS,
        GateName.SPEC_EARS,
        GateName.SPEC_AC_HOLDOUT_MAP,
        GateName.SPEC_ADR_CITED,
    ]


def test_each_gate_fails_with_the_item_named(tmp_path: Path) -> None:
    missing_section = SPEC_TEXT.replace("## Constraints\n\nNo ADR applies.\n\n", "")
    outcome = run_spec_gates(missing_section, has_test=lambda t: True, worktree=tmp_path)
    first = outcome.first_failure
    assert first is not None and first.gate is GateName.SPEC_SECTIONS
    assert [v.item for v in first.violations] == ["Constraints"]

    bad_ears = SPEC_TEXT.replace(
        "2. WHEN the terms are zero days THE SYSTEM SHALL expose due_date equal to\n   issued_at.",
        "2. The system returns issued_at for zero terms.",
    )
    ears = spec_ears(parse_spec(bad_ears))
    assert not ears.passed and [v.item for v in ears.violations] == ["AC2"]

    unmapped = SPEC_TEXT.replace(
        "- AC2 -> tests/acceptance/test_invoice_due_date_holdout.py::test_zero_terms\n", ""
    )
    mapping, missing = spec_ac_holdout_map(parse_spec(unmapped), lambda t: True)
    assert not mapping.passed and [v.item for v in mapping.violations] == ["AC2"] and missing == []

    absent, missing = spec_ac_holdout_map(
        parse_spec(SPEC_TEXT), lambda t: t.endswith("test_due_date")
    )
    assert not absent.passed and missing == [
        "tests/acceptance/test_invoice_due_date_holdout.py::test_zero_terms"
    ]

    no_adr = SPEC_TEXT.replace("No ADR applies.", "Keep it simple.")
    adr = run_spec_gates(no_adr, has_test=lambda t: True, worktree=tmp_path).results[3]
    assert not adr.passed and "No ADR applies." in adr.violations[0].problem
    (tmp_path / "docs" / "adr").mkdir(parents=True)
    (tmp_path / "docs" / "adr" / "0007-billing-dates.md").write_text("# 7\n", encoding="utf-8")
    cited = SPEC_TEXT.replace(
        "No ADR applies.", "- docs/adr/0007-billing-dates.md\n- docs/adr/0099-missing.md"
    )
    adr = run_spec_gates(cited, has_test=lambda t: True, worktree=tmp_path).results[3]
    assert not adr.passed and [v.item for v in adr.violations] == ["docs/adr/0099-missing.md"]
    cited_ok = SPEC_TEXT.replace("No ADR applies.", "- docs/adr/0007-billing-dates.md")
    assert run_spec_gates(cited_ok, has_test=lambda t: True, worktree=tmp_path).results[3].passed

    questions = SPEC_TEXT.replace(
        "## Open questions\n\nNone.", "## Open questions\n\n- Which timezone?\n- None of the above?"
    )
    assert parse_spec(questions).open_questions == ("Which timezone?", "None of the above?")


def test_messages_in_both_languages() -> None:
    en = messages.open_questions(["Which timezone?"], "en")
    pt = messages.open_questions(["Qual fuso horário?"], "pt-BR")
    assert "write access" in en and "1. Which timezone?" in en and "factory:needs-human" in en
    assert (
        "acesso de escrita" in pt and "1. Qual fuso horário?" in pt and "factory:needs-human" in pt
    )
    assert "tests/acceptance/x.py::t" in messages.missing_holdout(["tests/acceptance/x.py::t"])


# --- the plan phase with the spec gate ----------------------------------------------


async def run_plan(
    trace_writer: TraceWriter,
    product: Path,
    tmp_path: Path,
    scripts: list[FakeScript],
    *,
    holdout: FakeHoldout | None = None,
    tagged: TaggedIssue | None = None,
    instrument: RiskInstrument | None = None,
) -> tuple[object, FakeGitHub, FakeHoldout]:
    cfg = make_config()
    planner = cfg.roster.planner.model_copy(
        update={"harness": "fake", "vendor": "anthropic", "timeout_s": 60}
    )
    cfg = cfg.model_copy(update={"roster": cfg.roster.model_copy(update={"planner": planner})})
    trace_writer.load_config_version(**cfg.config_version_row())
    trace_writer.insert_task(
        task_id=TASK,
        issue_number=418,
        title="Invoice due date",
        branch="agent/418",
        workspace_kind=WorkspaceKind.WORKTREE,
        budget_usd=6.0,
        admitted_by="rsouza",
        config_sha=cfg.config_sha,
        status=TaskStatus.RUNNING,
    )
    if (product / "specs" / "418-invoice-due-date.md").exists():
        run_git(["rm", "-q", "specs/418-invoice-due-date.md"], cwd=product)
        run_git(["commit", "-q", "-m", "no spec yet"], cwd=product)
    deps = BuildDeps(
        writer=trace_writer,
        config=cfg,
        settings=Settings(),
        harness=FakeHarness(scripts),
        factory_version="t",
    )
    wt = create_worktree(product, issue=418, worktrees_dir=tmp_path / "w")
    run_id = trace_writer.start_run(
        task_id=TASK, attempt=1, factory_version="t", config_sha=cfg.config_sha
    )
    github = FakeGitHub()
    fake_holdout = holdout or FakeHoldout(set(HOLDOUT_IDS))
    phase = PlanPhase(
        deps,
        task_id=TASK,
        worktree=wt,
        issue=tagged or issue(),
        run_id=run_id,
        holdout=fake_holdout,
        github=github,
        instrument=instrument,
    )
    return await phase.run(), github, fake_holdout


async def test_green_spec_passes_all_gates(
    trace_writer: TraceWriter, product: Path, tmp_path: Path
) -> None:
    outcome, github, holdout = await run_plan(trace_writer, product, tmp_path, [script(SPEC_TEXT)])
    assert outcome.written and outcome.spec is not None  # type: ignore[attr-defined]
    assert holdout.refreshes == 1 and github.calls == []
    gates = [
        r["gate"]
        for r in trace_writer.db.fetchall(
            "SELECT gate FROM gate_results ORDER BY created_at, rowid"
        )
    ]
    assert gates[-4:] == ["spec_sections", "spec_ears", "spec_ac_holdout_map", "spec_adr_cited"]
    assert verify_chain(trace_writer.db).ok


async def test_unmapped_criterion_is_corrected(
    trace_writer: TraceWriter, product: Path, tmp_path: Path
) -> None:
    unmapped = SPEC_TEXT.replace(
        "- AC2 -> tests/acceptance/test_invoice_due_date_holdout.py::test_zero_terms\n", ""
    )
    outcome, github, _ = await run_plan(
        trace_writer, product, tmp_path, [script(unmapped), script(SPEC_TEXT)]
    )
    assert outcome.written and outcome.planner_calls == 2 and outcome.corrections == 1  # type: ignore[attr-defined]
    assert github.calls == []


async def test_missing_holdout_blocks_at_once(
    trace_writer: TraceWriter, product: Path, tmp_path: Path
) -> None:
    holdout = FakeHoldout({"tests/acceptance/test_invoice_due_date_holdout.py::test_due_date"})
    outcome, github, _ = await run_plan(
        trace_writer, product, tmp_path, [script(SPEC_TEXT), script(SPEC_TEXT)], holdout=holdout
    )
    assert outcome.blocked_reason is BlockedReason.MISSING_HOLDOUT and outcome.planner_calls == 1  # type: ignore[attr-defined]
    assert ("add_labels", (418, ("factory:needs-human",))) in github.calls
    assert "test_zero_terms" in github.comments()[-1]
    task = trace_writer.get_task(TASK)
    assert task is not None and task["blocked_reason"] == "missing_holdout"


async def test_open_questions_post_in_the_issue_language(
    trace_writer: TraceWriter, product: Path, tmp_path: Path
) -> None:
    pt_body = (
        "## Outcome\n\nA fatura deve expor a data de vencimento para o cliente com os termos.\n\n"
        "## Acceptance criteria\n\n1. WHEN x THE SYSTEM SHALL y.\n"
    )
    questioning = SPEC_TEXT.replace(
        "## Open questions\n\nNone.", "## Open questions\n\n- Qual fuso horário?"
    )
    outcome, github, _ = await run_plan(
        trace_writer,
        product,
        tmp_path,
        [script(questioning, open_questions=["Os termos são em dias corridos?"], status="fail")],
        tagged=issue(pt_body),
    )
    assert outcome.blocked_reason is BlockedReason.OPEN_QUESTIONS  # type: ignore[attr-defined]
    comment = github.comments()[-1]
    assert (
        "acesso de escrita" in comment
        and "Qual fuso horário?" in comment
        and "dias corridos" in comment
    )
    assert ("add_labels", (418, ("factory:needs-human",))) in github.calls
    task = trace_writer.get_task(TASK)
    assert task is not None and task["spec_path"] == SPEC_REL


async def test_three_failing_specs_block_spec_gate(
    trace_writer: TraceWriter, product: Path, tmp_path: Path
) -> None:
    bad = SPEC_TEXT.replace("No ADR applies.", "Keep it simple.")
    outcome, github, _ = await run_plan(trace_writer, product, tmp_path, [script(bad)])
    assert outcome.blocked_reason is BlockedReason.SPEC_GATE and outcome.planner_calls == 3  # type: ignore[attr-defined]
    assert "spec_adr_cited" in outcome.evidence and github.calls == []  # type: ignore[attr-defined]


@pytest.mark.parametrize("text", ["None.", "none", "Nenhuma."])
def test_open_questions_none_variants(text: str) -> None:
    assert parse_spec(f"## Open questions\n\n{text}\n").open_questions == ()
