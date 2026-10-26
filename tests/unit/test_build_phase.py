"""Unit 13: the build phase state machine with the fake harness on a real repo."""

import json
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from orchestrator.config import load_factory_config
from orchestrator.harness.fake import FakeEffect, FakeHarness, FakeScript, FakeToolCall, FakeUsage
from orchestrator.issue_to_pr import run_build_slice
from orchestrator.models.config import FactoryConfig, QualityCommands
from orchestrator.models.enums import BlockedReason, WorkspaceKind
from orchestrator.models.settings import Settings
from orchestrator.phases.build import BuildDeps, parse_focused_tests
from orchestrator.trace import TraceWriter, verify_chain
from orchestrator.workspace.git import run_git
from orchestrator.workspace.worktree import Worktree, create_worktree

ROOT = Path(__file__).resolve().parents[2]
SPEC_FIXTURE = ROOT / "fixtures" / "specs" / "418-invoice-due-date.md"
SPEC_REL = "specs/418-invoice-due-date.md"
TASK_ID = "acme/atlas-api#418"

PY = (sys.executable, "-I", "-S", "-c")
OK = (*PY, "print('ok')")
FAIL = (*PY, "import sys; print('boom'); sys.exit(1)")

_GREEN_FIXTURE = FakeScript.from_file(ROOT / "fixtures" / "envelopes" / "builder_green.json")
SRC = _GREEN_FIXTURE.effects[0].content
TEST = _GREEN_FIXTURE.effects[1].content
ENVELOPE: dict[str, object] = {
    "status": "success",
    "summary": "Add due_date to Invoice",
    "changed_files": ["src/billing/invoice.py", "tests/unit/test_invoice_due_date.py"],
    "tests_added": ["tests/unit/test_invoice_due_date.py"],
    "commit_message": "feat(billing): invoice due_date (#418)",
    "notes_for_next_agent": "computed property",
}
USAGE = FakeUsage(input_tokens=1000, output_tokens=100, cost_usd=0.41)

GREEN = FakeScript(
    session_id="sess-green",
    tool_calls=(FakeToolCall(tool="Edit", args={"file_path": "src/billing/invoice.py"}),),
    effects=(
        FakeEffect(write="src/billing/invoice.py", content=SRC),
        FakeEffect(write="tests/unit/test_invoice_due_date.py", content=TEST),
    ),
    envelope=ENVELOPE,
    usage=USAGE,
)
CLAIM_WRONG = FakeScript(
    session_id="sess-wrong",
    effects=(FakeEffect(write="src/billing/invoice.py", content=SRC),),
    envelope=ENVELOPE,  # claims the test file it never wrote
    usage=USAGE,
)
PROTECTED = FakeScript(
    effects=(
        FakeEffect(write="src/billing/invoice.py", content=SRC),
        FakeEffect(
            write="tests/acceptance/test_holdout.py", content="def test_h() -> None:\n    pass\n"
        ),
    ),
    envelope={**ENVELOPE, "changed_files": ["src/billing/invoice.py"], "tests_added": []},
)
DEPENDENCY = FakeScript(
    effects=(FakeEffect(write="pyproject.toml", content="[project]\nname='x'\n"),),
    envelope={**ENVELOPE, "changed_files": ["pyproject.toml"], "tests_added": []},
)
INVALID = FakeScript(envelope_text="not json at all")
SLEEPY = FakeScript(sleep_s=30, envelope=ENVELOPE)
INFRA = FakeScript(fail_with="API 529 overloaded")
MAX_BUDGET = FakeScript(fail_with="error_max_budget_usd: cap reached")
MAX_TURNS = FakeScript(fail_with="error_max_turns: Reached max turns (80)")


def make_config(
    *, builder_timeout_s: int = 60, quality: QualityCommands | None = None
) -> FactoryConfig:
    cfg = load_factory_config(ROOT / "fixtures" / "factory_config")
    builder = cfg.roster.builder.model_copy(
        update={"harness": "fake", "vendor": "anthropic", "timeout_s": builder_timeout_s}
    )
    roster = cfg.roster.model_copy(update={"builder": builder})
    commands = quality or QualityCommands(format=OK, lint=OK, typecheck=OK, test=OK, full_suite=OK)
    return cfg.model_copy(update={"roster": roster, "quality": commands})


class Recorder:
    def __init__(self) -> None:
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def deps_for(
    trace_writer: TraceWriter,
    scripts: Sequence[FakeScript],
    *,
    config: FactoryConfig | None = None,
) -> tuple[BuildDeps, FakeHarness, Recorder]:
    cfg = config or make_config()
    trace_writer.load_config_version(**cfg.config_version_row())
    trace_writer.insert_task(
        task_id=TASK_ID,
        issue_number=418,
        title="Invoice due date",
        branch="agent/418",
        workspace_kind=WorkspaceKind.WORKTREE,
        budget_usd=6.0,
        admitted_by="rsouza",
        config_sha=cfg.config_sha,
    )
    harness = FakeHarness(scripts)
    recorder = Recorder()
    deps = BuildDeps(
        writer=trace_writer,
        config=cfg,
        settings=Settings(),
        harness=harness,
        sleep=recorder.sleep,
        factory_version="0.1.0-test",
    )
    return deps, harness, recorder


def worktree_for(product: Path, tmp_path: Path) -> Worktree:
    return create_worktree(product, issue=418, worktrees_dir=tmp_path / "worktrees")


def phases(trace_writer: TraceWriter, run_id: str) -> list[tuple[str, int, str]]:
    rows = trace_writer.db.fetchall(
        "SELECT name, attempt, status FROM phases WHERE run_id = ? ORDER BY seq", (run_id,)
    )
    return [(r["name"], r["attempt"], r["status"]) for r in rows]


def gates(trace_writer: TraceWriter, run_id: str) -> list[tuple[str, int]]:
    rows = trace_writer.db.fetchall(
        "SELECT gate, passed FROM gate_results WHERE run_id = ? ORDER BY created_at, rowid",
        (run_id,),
    )
    return [(r["gate"], r["passed"]) for r in rows]


async def test_green_path_commits_and_records_everything(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    deps, harness, _ = deps_for(trace_writer, [GREEN])
    wt = worktree_for(product, tmp_path)
    outcome = await run_build_slice(deps, task_id=TASK_ID, worktree=wt, spec_rel=SPEC_REL)
    assert outcome.passed and outcome.repairs == 0 and outcome.builder_calls == 1
    assert outcome.commit_sha is not None
    assert outcome.cost_usd == pytest.approx(0.41)
    head = run_git(["log", "-1", "--format=%H %s %an"], cwd=wt.path).stdout.strip()
    assert head.startswith(outcome.commit_sha)
    assert "feat(billing): invoice due_date (#418)" in head and head.endswith("factory")
    assert phases(trace_writer, outcome.run_id) == [
        ("build", 1, "pass"),
        ("code_gates", 1, "pass"),
        ("commit", 1, "pass"),
    ]
    assert gates(trace_writer, outcome.run_id) == [
        ("envelope_schema", 1),
        ("permissions_enforce", 1),
        ("artifacts_exist", 1),
        ("files_non_empty", 1),
        ("diff_matches_claims", 1),
        ("format", 1),
        ("lint", 1),
        ("typecheck", 1),
        ("focused_tests", 1),
    ]
    run = trace_writer.get_run(outcome.run_id)
    assert run is not None and run["status"] == "success"
    assert run["total_cost_usd"] == pytest.approx(0.41)
    task = trace_writer.get_task(TASK_ID)
    assert task is not None and task["status"] == "running"
    assert task["spent_usd"] == pytest.approx(0.41)
    kinds = [r["kind"] for r in trace_writer.db.fetchall("SELECT kind FROM events ORDER BY seq")]
    assert kinds.count("agent_start") == 1 and kinds.count("agent_end") == 1
    assert "tool_call" in kinds and "handoff" in kinds
    agent_start = trace_writer.db.fetchone(
        "SELECT payload_json FROM events WHERE kind = 'agent_start'"
    )
    assert agent_start is not None
    payload = json.loads(agent_start["payload_json"])
    assert payload["harness"] == "fake" and len(payload["guide_sha"]) == 64
    assert payload["gen_ai.request.model"] == "claude-opus-5-5"
    request = harness.calls[0]
    assert request.resume_session_id is None
    assert "tests/unit/test_invoice_due_date.py::test_due_date_adds_terms" in request.prompt
    assert verify_chain(trace_writer.db).ok


async def test_wrong_claim_costs_one_repair(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    deps, harness, _ = deps_for(trace_writer, [CLAIM_WRONG, GREEN])
    wt = worktree_for(product, tmp_path)
    outcome = await run_build_slice(deps, task_id=TASK_ID, worktree=wt, spec_rel=SPEC_REL)
    assert outcome.passed and outcome.repairs == 1 and outcome.builder_calls == 2
    second = harness.calls[1]
    assert second.resume_session_id == "sess-wrong"
    assert "## Previous verification result" in second.prompt
    assert '"gate": "artifacts_exist"' in second.prompt
    assert phases(trace_writer, outcome.run_id) == [
        ("build", 1, "pass"),
        ("code_gates", 1, "fail"),
        ("build", 2, "pass"),
        ("code_gates", 2, "pass"),
        ("commit", 1, "pass"),
    ]
    assert ("artifacts_exist", 0) in gates(trace_writer, outcome.run_id)


async def test_fourth_failure_blocks_with_repair_cap(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    deps, harness, _ = deps_for(trace_writer, [CLAIM_WRONG])
    wt = worktree_for(product, tmp_path)
    outcome = await run_build_slice(deps, task_id=TASK_ID, worktree=wt, spec_rel=SPEC_REL)
    assert not outcome.passed
    assert outcome.blocked_reason is BlockedReason.REPAIR_CAP
    assert outcome.builder_calls == 4 and outcome.repairs == 3
    assert "artifacts_exist" in outcome.evidence
    task = trace_writer.get_task(TASK_ID)
    assert task is not None and (task["status"], task["blocked_reason"]) == (
        "blocked",
        "repair_cap",
    )
    run = trace_writer.get_run(outcome.run_id)
    assert run is not None and run["status"] == "fail"
    assert [p[1] for p in phases(trace_writer, outcome.run_id) if p[0] == "build"] == [1, 2, 3, 4]
    assert all(c.resume_session_id == "sess-wrong" for c in harness.calls[1:])


async def test_protected_edit_blocks_at_once(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    deps, _, _ = deps_for(trace_writer, [PROTECTED, GREEN])
    wt = worktree_for(product, tmp_path)
    outcome = await run_build_slice(deps, task_id=TASK_ID, worktree=wt, spec_rel=SPEC_REL)
    assert outcome.blocked_reason is BlockedReason.PROTECTED_PATH
    assert outcome.builder_calls == 1 and outcome.repairs == 0
    assert (
        (wt.path / "tests/acceptance/test_holdout.py")
        .read_text(encoding="utf-8")
        .endswith("assert True\n")
    )
    kinds = [r["kind"] for r in trace_writer.db.fetchall("SELECT kind FROM events ORDER BY seq")]
    assert "incident" in kinds
    assert run_git(["status", "--porcelain"], cwd=wt.path).stdout.strip() != ""  # src edit kept
    assert gates(trace_writer, outcome.run_id)[-1] == ("permissions_enforce", 0)


async def test_dependency_change_blocks(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    deps, _, _ = deps_for(trace_writer, [DEPENDENCY])
    wt = worktree_for(product, tmp_path)
    outcome = await run_build_slice(deps, task_id=TASK_ID, worktree=wt, spec_rel=SPEC_REL)
    assert outcome.blocked_reason is BlockedReason.DEPENDENCY_CHANGE
    assert (wt.path / "pyproject.toml").read_text(encoding="utf-8") == "[project]\nname = 'demo'\n"


async def test_invalid_envelope_reprompts_once(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    deps, harness, _ = deps_for(trace_writer, [INVALID, GREEN])
    wt = worktree_for(product, tmp_path)
    outcome = await run_build_slice(deps, task_id=TASK_ID, worktree=wt, spec_rel=SPEC_REL)
    assert outcome.passed and outcome.builder_calls == 2 and outcome.repairs == 0
    assert '"gate": "envelope_schema"' in harness.calls[1].prompt
    envelopes = trace_writer.db.fetchall("SELECT valid FROM envelopes ORDER BY created_at, rowid")
    assert [e["valid"] for e in envelopes] == [0, 1]


async def test_two_invalid_envelopes_block(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    deps, _, _ = deps_for(trace_writer, [INVALID])
    wt = worktree_for(product, tmp_path)
    outcome = await run_build_slice(deps, task_id=TASK_ID, worktree=wt, spec_rel=SPEC_REL)
    assert outcome.blocked_reason is BlockedReason.REPAIR_CAP and outcome.builder_calls == 2
    assert "not JSON" in outcome.evidence


async def test_timeout_blocks(product: Path, tmp_path: Path, trace_writer: TraceWriter) -> None:
    deps, _, _ = deps_for(trace_writer, [SLEEPY], config=make_config(builder_timeout_s=1))
    wt = worktree_for(product, tmp_path)
    outcome = await run_build_slice(deps, task_id=TASK_ID, worktree=wt, spec_rel=SPEC_REL)
    assert outcome.blocked_reason is BlockedReason.TIMEOUT
    assert phases(trace_writer, outcome.run_id) == [("build", 1, "fail")]


async def test_infra_errors_retry_without_consuming_repairs(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    deps, _, recorder = deps_for(trace_writer, [INFRA, GREEN])
    wt = worktree_for(product, tmp_path)
    outcome = await run_build_slice(deps, task_id=TASK_ID, worktree=wt, spec_rel=SPEC_REL)
    assert outcome.passed and outcome.repairs == 0 and outcome.builder_calls == 2
    assert recorder.sleeps == [5]
    assert [p[1] for p in phases(trace_writer, outcome.run_id) if p[0] == "build"] == [1, 1]


async def test_persistent_infra_errors_block(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    deps, _, recorder = deps_for(trace_writer, [INFRA])
    wt = worktree_for(product, tmp_path)
    outcome = await run_build_slice(deps, task_id=TASK_ID, worktree=wt, spec_rel=SPEC_REL)
    assert outcome.blocked_reason is BlockedReason.TIMEOUT
    assert outcome.builder_calls == 4
    assert recorder.sleeps == [5, 20, 60]


@pytest.mark.parametrize(
    ("script", "reason"),
    [(MAX_BUDGET, BlockedReason.BUDGET), (MAX_TURNS, BlockedReason.REPAIR_CAP)],
)
async def test_agent_side_stops_block_with_their_reason(
    product: Path,
    tmp_path: Path,
    trace_writer: TraceWriter,
    script: FakeScript,
    reason: BlockedReason,
) -> None:
    deps, _, _ = deps_for(trace_writer, [script])
    wt = worktree_for(product, tmp_path)
    outcome = await run_build_slice(deps, task_id=TASK_ID, worktree=wt, spec_rel=SPEC_REL)
    assert outcome.blocked_reason is reason and outcome.builder_calls == 1


async def test_quality_failure_is_repaired(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    flaky = QualityCommands(format=OK, lint=FAIL, typecheck=OK, test=OK, full_suite=OK)
    deps, harness, _ = deps_for(trace_writer, [GREEN], config=make_config(quality=flaky))
    wt = worktree_for(product, tmp_path)
    outcome = await run_build_slice(deps, task_id=TASK_ID, worktree=wt, spec_rel=SPEC_REL)
    assert outcome.blocked_reason is BlockedReason.REPAIR_CAP
    assert outcome.builder_calls == 4
    assert '"gate": "lint"' in harness.calls[1].prompt
    assert "boom" in harness.calls[1].prompt
    assert gates(trace_writer, outcome.run_id).count(("lint", 0)) == 4


def test_parse_focused_tests() -> None:
    text = SPEC_FIXTURE.read_text(encoding="utf-8")
    assert parse_focused_tests(text) == [
        "tests/unit/test_invoice_due_date.py::test_due_date_adds_terms",
        "tests/unit/test_invoice_due_date.py::test_zero_terms_is_same_day",
    ]
    assert parse_focused_tests("no plan here") == []
