"""Unit 39: the full suite with the holdout on the final SHA."""

import json
import sys
from pathlib import Path

from orchestrator.harness.fake import FakeEffect, FakeScript
from orchestrator.models.config import QualityCommands
from orchestrator.phases.build import BuildDeps
from orchestrator.phases.full_suite import FullSuitePhase, failing_tests, holdout_copied
from orchestrator.trace import TraceWriter
from orchestrator.workspace.git import run_git
from orchestrator.workspace.holdout import HoldoutCheckout
from orchestrator.workspace.worktree import Worktree
from tests.unit.test_build_phase import (
    ENVELOPE,
    GREEN,
    OK,
    SPEC_REL,
    TASK_ID,
    USAGE,
    deps_for,
    make_config,
    worktree_for,
)
from tests.unit.test_container import IMAGE, FakeDocker
from tests.unit.test_review_phase import ACCEPTED
from tests.unit.test_runner import PLAN, Stack, prepare
from tests.unit.test_spec_gate import TASK

PYTEST_SUITE = (sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/acceptance")
HOLDOUT_TEST = (
    "from pathlib import Path\n\n\n"
    "def test_fixed() -> None:\n"
    "    assert Path('src/fixed.txt').exists()\n"
)
FIXES = FakeScript(
    session_id="sess-fix",
    effects=(FakeEffect(write="src/fixed.txt", content="fixed\n"),),
    envelope={**ENVELOPE, "changed_files": ["src/fixed.txt"], "tests_added": []},
    usage=USAGE,
)
WRONG_FIX = FakeScript(
    session_id="sess-wrong-fix",
    effects=(FakeEffect(write="src/notes.txt", content="tried\n"),),
    envelope={**ENVELOPE, "changed_files": ["src/notes.txt"], "tests_added": []},
    usage=USAGE,
)
PYTEST_OUTPUT = (
    "F.\n"
    "=== FAILURES ===\n"
    "FAILED tests/acceptance/test_a.py::test_one - AssertionError\n"
    "FAILED tests/acceptance/test_a.py::test_one - repeated\n"
    "ERROR tests/acceptance/test_b.py::test_two\n"
    "1 failed, 1 passed in 0.10s\n"
)


def new_run(trace_writer: TraceWriter, deps: BuildDeps) -> str:
    return trace_writer.start_run(
        task_id=TASK_ID,
        attempt=1,
        factory_version=deps.factory_version,
        config_sha=deps.config.config_sha,
    )


def holdout_for(tmp_path: Path, *, failing: bool = True) -> HoldoutCheckout:
    """A real holdout checkout cloned from a tiny origin repository."""
    origin = tmp_path / "holdout-origin"
    tests = origin / "tests" / "acceptance"
    tests.mkdir(parents=True)
    (tests / "__init__.py").write_text("", encoding="utf-8")
    body = HOLDOUT_TEST if failing else "def test_fine() -> None:\n    assert True\n"
    (tests / "test_holdout_due.py").write_text(body, encoding="utf-8")
    run_git(["init", "-q", "-b", "main"], cwd=origin)
    run_git(["config", "user.email", "dev@example.test"], cwd=origin)
    run_git(["config", "user.name", "Dev"], cwd=origin)
    run_git(["add", "-A"], cwd=origin)
    run_git(["commit", "-q", "-m", "holdout"], cwd=origin)
    return HoldoutCheckout(str(origin), tmp_path / "holdout")


def built(product: Path, tmp_path: Path) -> Worktree:
    """A worktree whose final SHA is the green build (committed, clean)."""
    wt = worktree_for(product, tmp_path)
    (wt.path / "src" / "billing" / "invoice.py").write_text("built = True\n", encoding="utf-8")
    run_git(["add", "-A"], cwd=wt.path)
    run_git(
        ["-c", "user.name=f", "-c", "user.email=f@x", "commit", "-q", "-m", "build"], cwd=wt.path
    )
    return wt


def clean(wt: Worktree) -> bool:
    return run_git(["status", "--porcelain"], cwd=wt.path).stdout.strip() == ""


def phase_rows(trace_writer: TraceWriter, run_id: str) -> list[tuple[str, int, str]]:
    rows = trace_writer.db.fetchall(
        "SELECT name, attempt, status FROM phases WHERE run_id = ? ORDER BY seq", (run_id,)
    )
    return [(r["name"], r["attempt"], r["status"]) for r in rows]


def test_failing_tests_parses_a_pytest_summary() -> None:
    assert failing_tests(PYTEST_OUTPUT) == [
        "tests/acceptance/test_a.py::test_one",
        "tests/acceptance/test_b.py::test_two",
    ]
    assert failing_tests("all green") == []


def test_holdout_copied_leaves_the_worktree_as_it_was(product: Path, tmp_path: Path) -> None:
    wt = built(product, tmp_path)
    holdout = holdout_for(tmp_path)
    holdout.refresh()
    committed = wt.path / "tests" / "acceptance" / "test_holdout.py"
    assert committed.is_file()  # the product fixture commits a placeholder holdout file
    with holdout_copied(holdout, wt.path) as destination:
        assert (destination / "test_holdout_due.py").is_file()
        committed.write_text("tampered\n", encoding="utf-8")
    assert not (wt.path / "tests" / "acceptance" / "test_holdout_due.py").exists()
    assert committed.read_text(encoding="utf-8").startswith("def test_h")
    assert clean(wt)


async def test_repair_fixes_the_holdout_failure(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    commands = QualityCommands(format=OK, lint=OK, typecheck=OK, test=OK, full_suite=PYTEST_SUITE)
    deps, harness, _ = deps_for(trace_writer, [FIXES], config=make_config(quality=commands))
    wt = built(product, tmp_path)
    before = run_git(["rev-parse", "HEAD"], cwd=wt.path).stdout.strip()
    run_id = new_run(trace_writer, deps)
    outcome = await FullSuitePhase(
        deps,
        task_id=TASK_ID,
        worktree=wt,
        spec_rel=SPEC_REL,
        run_id=run_id,
        holdout=holdout_for(tmp_path),
        issue_number=418,
        build_repairs=1,
    ).run()
    assert outcome.passed and outcome.repairs == 1 and outcome.failing_tests == ()
    assert outcome.head_sha is not None and outcome.head_sha != before
    assert phase_rows(trace_writer, run_id) == [
        ("full_suite", 1, "fail"),
        ("build", 1, "pass"),
        ("code_gates", 1, "pass"),
        ("commit", 1, "pass"),
        ("full_suite", 2, "pass"),
    ]
    request = harness.calls[0]
    assert "tests/acceptance/test_holdout_due.py::test_fixed" in request.prompt
    gates = trace_writer.db.fetchall(
        "SELECT gate, passed, output_tail FROM gate_results"
        " WHERE run_id = ? AND gate = 'full_suite' ORDER BY created_at, rowid",
        (run_id,),
    )
    assert [(g["gate"], g["passed"]) for g in gates] == [("full_suite", 0), ("full_suite", 1)]
    assert "test_fixed" in gates[0]["output_tail"]
    assert not (wt.path / "tests" / "acceptance" / "test_holdout_due.py").exists()
    assert clean(wt)
    log = trace_writer.db.fetchone(
        "SELECT payload_json FROM events"
        " WHERE kind = 'log' AND payload_json LIKE '%full_suite_repair%'"
    )
    assert log is not None and json.loads(log["payload_json"])["repairs_counted"] == 2


async def test_second_failure_blocks_with_the_failing_tests(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    commands = QualityCommands(format=OK, lint=OK, typecheck=OK, test=OK, full_suite=PYTEST_SUITE)
    deps, _, _ = deps_for(trace_writer, [WRONG_FIX], config=make_config(quality=commands))
    wt = built(product, tmp_path)
    run_id = new_run(trace_writer, deps)
    outcome = await FullSuitePhase(
        deps,
        task_id=TASK_ID,
        worktree=wt,
        spec_rel=SPEC_REL,
        run_id=run_id,
        holdout=holdout_for(tmp_path),
        issue_number=418,
    ).run()
    assert not outcome.passed and outcome.blocked_reason == "full_suite"
    assert outcome.repairs == 1
    assert outcome.failing_tests == ("tests/acceptance/test_holdout_due.py::test_fixed",)
    first_line = outcome.evidence.splitlines()[0]
    assert (
        "after one builder repair" in first_line and "test_holdout_due.py::test_fixed" in first_line
    )
    assert [p[0] for p in phase_rows(trace_writer, run_id)] == [
        "full_suite",
        "build",
        "code_gates",
        "commit",
        "full_suite",
    ]
    assert phase_rows(trace_writer, run_id)[-1] == ("full_suite", 2, "fail")
    assert clean(wt)


async def test_no_room_in_the_repair_cap_blocks_at_once(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    commands = QualityCommands(format=OK, lint=OK, typecheck=OK, test=OK, full_suite=PYTEST_SUITE)
    deps, harness, _ = deps_for(trace_writer, [FIXES], config=make_config(quality=commands))
    wt = built(product, tmp_path)
    run_id = new_run(trace_writer, deps)
    outcome = await FullSuitePhase(
        deps,
        task_id=TASK_ID,
        worktree=wt,
        spec_rel=SPEC_REL,
        run_id=run_id,
        holdout=holdout_for(tmp_path),
        issue_number=418,
        build_repairs=3,
    ).run()
    assert not outcome.passed and outcome.repairs == 0 and harness.calls == []
    assert "no repair left in the cap" in outcome.evidence.splitlines()[0]
    assert phase_rows(trace_writer, run_id) == [("full_suite", 1, "fail")]


async def test_green_holdout_passes_without_a_repair(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    commands = QualityCommands(format=OK, lint=OK, typecheck=OK, test=OK, full_suite=PYTEST_SUITE)
    deps, harness, _ = deps_for(trace_writer, [GREEN], config=make_config(quality=commands))
    wt = built(product, tmp_path)
    run_id = new_run(trace_writer, deps)
    outcome = await FullSuitePhase(
        deps,
        task_id=TASK_ID,
        worktree=wt,
        spec_rel=SPEC_REL,
        run_id=run_id,
        holdout=holdout_for(tmp_path, failing=False),
        issue_number=418,
    ).run()
    assert outcome.passed and outcome.repairs == 0 and harness.calls == []
    assert phase_rows(trace_writer, run_id) == [("full_suite", 1, "pass")]


async def test_container_mode_mounts_the_holdout_only_into_a_fresh_container(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    prepare(product, tmp_path)
    docker = FakeDocker()
    stack = Stack(
        trace_writer,
        product,
        tmp_path,
        [PLAN, GREEN, ACCEPTED],
        workspace="container",
        docker=docker,
        task_image=IMAGE,
    )
    stack.admit()
    report = await stack.runner.run(TASK)
    assert report.delivered
    assert stack.phases(report.run_id)[-3:] == ["full_suite", "deliver", "ci_wait"]
    creates = {c[c.index("--name") + 1]: c for c in docker.calls if c[0] == "create"}
    builder, suite = creates["factory-task-418-1"], creates["factory-suite-418-1"]
    assert not any("/work/tests/acceptance" in flag for flag in builder)
    holdout_flags = [f for f in suite if f.endswith(":/work/tests/acceptance:ro")]
    assert len(holdout_flags) == 1 and str(stack.holdout.tests_dir) in holdout_flags[0]
    assert not any("/opt/factory/hooks" in flag for flag in suite)
    assert ["rm", "-f", "factory-suite-418-1"] in docker.calls and docker.containers == {}
    suite_execs = [e for e in docker.execs if e[1]["FACTORY_CONTAINER"] == "factory-suite-418-1"]
    assert suite_execs and all(
        "ANTHROPIC_API_KEY" not in env and "OPENAI_API_KEY" not in env for _, env, _ in suite_execs
    )
