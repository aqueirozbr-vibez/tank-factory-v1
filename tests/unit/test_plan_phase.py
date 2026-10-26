"""Unit 22: planner prompt and plan phase with the fake harness."""

import json
from pathlib import Path

import pytest

from orchestrator.errors import PromptError
from orchestrator.harness.fake import FakeEffect, FakeHarness, FakeScript, FakeUsage
from orchestrator.intake.issue_fetcher import TaggedComment, TaggedIssue, parse_sections
from orchestrator.models.enums import AgentRole, BlockedReason, TaskStatus, WorkspaceKind
from orchestrator.models.settings import Settings
from orchestrator.phases.build import BuildDeps
from orchestrator.phases.plan import (
    PlanPhase,
    adr_index,
    repository_guidance,
    slugify,
    spec_path_for,
)
from orchestrator.prompt_builder import build_planner_prompt, load_guide, load_prompt_text
from orchestrator.trace import TraceWriter, verify_chain
from orchestrator.workspace.git import run_git
from orchestrator.workspace.worktree import create_worktree
from tests.unit.test_build_phase import make_config

ROOT = Path(__file__).resolve().parents[2]
SPEC_TEXT = (ROOT / "fixtures" / "specs" / "418-invoice-due-date.md").read_text(encoding="utf-8")
SPEC_REL = "specs/418-invoice-due-date.md"
TASK = "acme/atlas-api#418"
ISSUE_BODY = json.loads(
    (ROOT / "fixtures" / "github" / "issue_418.json").read_text(encoding="utf-8")
)["body"]

ENVELOPE: dict[str, object] = {
    "status": "success",
    "summary": "Spec for the invoice due date",
    "artifacts": [SPEC_REL],
    "notes_for_next_agent": "computed property, no migration",
    "risk_inputs": {
        "tiers_touched": ["C"],
        "est_lines": 60,
        "new_dependencies": [],
        "modules": ["billing"],
    },
    "open_questions": [],
}
USAGE = FakeUsage(input_tokens=5000, output_tokens=900, cost_usd=0.22)
GREEN = FakeScript(
    session_id="plan-1",
    effects=(FakeEffect(write=SPEC_REL, content=SPEC_TEXT),),
    envelope=ENVELOPE,
    usage=USAGE,
)
OUTSIDE = FakeScript(
    effects=(
        FakeEffect(write=SPEC_REL, content=SPEC_TEXT),
        FakeEffect(write="src/x.py", content="x = 1\n"),
    ),
    envelope=ENVELOPE,
)
NO_FILE = FakeScript(
    session_id="plan-2", envelope=ENVELOPE, usage=USAGE
)  # claims a spec it never wrote
INVALID = FakeScript(envelope_text="{not json")


class _Holdout:
    @property
    def tests_dir(self) -> Path:
        return Path("holdout") / "tests" / "acceptance"

    def refresh(self) -> str:
        return "0" * 40

    def has_test(self, test_id: str) -> bool:
        return test_id.startswith("tests/acceptance/")

    def copy_into(self, workspace: Path) -> Path:
        return workspace / "tests" / "acceptance"


def tagged_issue() -> TaggedIssue:
    return TaggedIssue(
        number=418,
        title="Invoice due date",
        author_login="rsouza",
        author_association="MEMBER",
        trusted=True,
        body=ISSUE_BODY,
        sections=parse_sections(ISSUE_BODY),
        comments=(
            TaggedComment(
                1, "rsouza", "MEMBER", True, "Terms default to 30 days.", "2026-10-23T13:05:00Z"
            ),
        ),
    )


def prepare(product: Path, *, guidance: str = "# Agents\n\nUse pytest.\n") -> None:
    (product / "AGENTS.md").write_text(guidance, encoding="utf-8")
    (product / "docs" / "adr").mkdir(parents=True)
    (product / "docs" / "adr" / "0007-billing-dates.md").write_text(
        "# 7. Billing dates are UTC\n\nAccepted.\n", encoding="utf-8"
    )
    run_git(["add", "-A"], cwd=product)
    run_git(["commit", "-q", "-m", "guidance and adr"], cwd=product)


def deps_for(trace_writer: TraceWriter, scripts: list[FakeScript]) -> BuildDeps:
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
    return BuildDeps(
        writer=trace_writer,
        config=cfg,
        settings=Settings(),
        harness=FakeHarness(scripts),
        factory_version="t",
    )


async def run_plan(
    trace_writer: TraceWriter, product: Path, tmp_path: Path, scripts: list[FakeScript]
) -> tuple[object, FakeHarness]:
    deps = deps_for(trace_writer, scripts)
    if (product / "specs" / "418-invoice-due-date.md").exists():
        # the shared fixture ships the spec; the planner must be the one to write it
        run_git(["rm", "-q", "specs/418-invoice-due-date.md"], cwd=product)
        run_git(["commit", "-q", "-m", "no spec yet"], cwd=product)
    wt = create_worktree(product, issue=418, worktrees_dir=tmp_path / "w")
    run_id = trace_writer.start_run(
        task_id=TASK, attempt=1, factory_version="t", config_sha=deps.config.config_sha
    )
    phase = PlanPhase(
        deps, task_id=TASK, worktree=wt, issue=tagged_issue(), run_id=run_id, holdout=_Holdout()
    )
    outcome = await phase.run()
    assert isinstance(deps.harness, FakeHarness)
    return outcome, deps.harness


def test_slug_and_helpers(product: Path) -> None:
    assert slugify("Invoice due date") == "invoice-due-date"
    assert slugify("  ÀÉ!! ") == "task"
    assert spec_path_for(418, "Invoice due date") == SPEC_REL
    assert spec_path_for(7, None) == "specs/7-task.md"
    assert repository_guidance(product) is None and adr_index(product) == []
    prepare(product)
    assert repository_guidance(product) == "# Agents\n\nUse pytest.\n"
    assert adr_index(product) == ["docs/adr/0007-billing-dates.md: 7. Billing dates are UTC"]


def test_planner_prompt_sections_and_secrets() -> None:
    guide = load_guide(AgentRole.PLANNER)
    template = load_prompt_text(AgentRole.PLANNER, "spec-template.md")
    bundle = build_planner_prompt(
        guide,
        spec_path=SPEC_REL,
        spec_template=template,
        issue_block=tagged_issue().render_for_agent(),
        agents_md="# Agents\n\nUse pytest.\n",
        adr_index=["docs/adr/0007-billing-dates.md: 7. Billing dates are UTC"],
    )
    text = bundle.user_prompt
    order = [
        text.index(h)
        for h in (
            "## Spec template and gate rules",
            "## Repository guidance",
            "## Architecture decision records",
            "## Issue",
        )
    ]
    assert order == sorted(order)
    assert f"Write the spec to `{SPEC_REL}`" in text
    assert "WHEN <condition> THE SYSTEM SHALL" in text
    assert "Use pytest." in text and "0007-billing-dates" in text
    assert '<issue number="418"' in text and "## Correction" not in text
    assert bundle.sections == ("spec_template", "agents_md", "adr_index", "issue")
    with_fix = build_planner_prompt(
        guide,
        spec_path=SPEC_REL,
        spec_template=template,
        issue_block="<issue/>",
        correction="Gate x failed.",
    )
    assert with_fix.sections[-1] == "correction" and "## Correction" in with_fix.user_prompt
    assert "No CLAUDE.md or AGENTS.md" in with_fix.user_prompt and "No ADRs" in with_fix.user_prompt
    with pytest.raises(PromptError, match="credential"):
        build_planner_prompt(
            guide,
            spec_path=SPEC_REL,
            spec_template=template,
            issue_block="<issue/>",
            agents_md="token ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        )
    with pytest.raises(PromptError, match="planner guide"):
        build_planner_prompt(
            load_guide(AgentRole.BUILDER),
            spec_path=SPEC_REL,
            spec_template=template,
            issue_block="x",
        )


async def test_green_plan_writes_the_spec(
    trace_writer: TraceWriter, product: Path, tmp_path: Path
) -> None:
    prepare(product)
    outcome, harness = await run_plan(trace_writer, product, tmp_path, [GREEN])
    assert outcome.written and outcome.planner_calls == 1 and outcome.corrections == 0  # type: ignore[attr-defined]
    assert outcome.spec_text == SPEC_TEXT  # type: ignore[attr-defined]
    assert outcome.envelope.risk_inputs.est_lines == 60  # type: ignore[attr-defined]
    task = trace_writer.get_task(TASK)
    assert (
        task is not None
        and task["spec_path"] == SPEC_REL
        and task["spent_usd"] == pytest.approx(0.22)
    )
    phases = trace_writer.db.fetchall("SELECT name, status FROM phases ORDER BY seq")
    assert [(p["name"], p["status"]) for p in phases] == [
        ("plan", "pass"),
        ("spec_gate", "pass"),
        ("commit", "pass"),
    ]
    gates = [
        r["gate"]
        for r in trace_writer.db.fetchall(
            "SELECT gate FROM gate_results ORDER BY created_at, rowid"
        )
    ]
    assert gates == [
        "envelope_schema",
        "permissions_enforce",
        "artifacts_exist",
        "files_non_empty",
        "diff_matches_claims",
        "spec_sections",
        "spec_ears",
        "spec_ac_holdout_map",
        "spec_adr_cited",
    ]
    prompt = harness.calls[0].prompt
    assert (
        '<issue number="418"' in prompt
        and "Use pytest." in prompt
        and "0007-billing-dates" in prompt
    )
    assert harness.calls[0].tools == ("Read", "Glob", "Grep", "Write", "Edit")
    assert verify_chain(trace_writer.db).ok


async def test_write_outside_specs_blocks(
    trace_writer: TraceWriter, product: Path, tmp_path: Path
) -> None:
    outcome, _ = await run_plan(trace_writer, product, tmp_path, [OUTSIDE, GREEN])
    assert outcome.blocked_reason is BlockedReason.PROTECTED_PATH and outcome.planner_calls == 1  # type: ignore[attr-defined]
    assert not (tmp_path / "w" / "agent-418" / "src" / "x.py").exists()


async def test_invalid_envelope_and_claim_corrections(
    trace_writer: TraceWriter, product: Path, tmp_path: Path
) -> None:
    outcome, harness = await run_plan(trace_writer, product, tmp_path, [INVALID, NO_FILE, GREEN])
    assert outcome.written and outcome.planner_calls == 3 and outcome.corrections == 1  # type: ignore[attr-defined]
    assert "## Correction" in harness.calls[1].prompt and "not JSON" in harness.calls[1].prompt
    assert "artifacts_exist" in harness.calls[2].prompt
    assert harness.calls[2].resume_session_id == "plan-2"


async def test_three_claim_failures_block_spec_gate(
    trace_writer: TraceWriter, product: Path, tmp_path: Path
) -> None:
    outcome, _ = await run_plan(trace_writer, product, tmp_path, [NO_FILE])
    assert outcome.blocked_reason is BlockedReason.SPEC_GATE and outcome.planner_calls == 3  # type: ignore[attr-defined]
    assert outcome.corrections == 2  # type: ignore[attr-defined]


async def test_secret_in_guidance_blocks_before_any_call(
    trace_writer: TraceWriter, product: Path, tmp_path: Path
) -> None:
    prepare(product, guidance="# Agents\n\nANTHROPIC_API_KEY=supersecretvalue\n")
    outcome, harness = await run_plan(trace_writer, product, tmp_path, [GREEN])
    assert outcome.blocked_reason is BlockedReason.SPEC_GATE and outcome.planner_calls == 0  # type: ignore[attr-defined]
    assert "credential" in outcome.evidence and harness.calls == []  # type: ignore[attr-defined]
