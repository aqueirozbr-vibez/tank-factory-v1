"""Unit 08: permissions snapshot, diff, classification and rollback on real git repos."""

import json
from pathlib import Path

import pytest

from orchestrator import constants
from orchestrator.gates.base import GateContext
from orchestrator.gates.permissions import PathChange as Change
from orchestrator.gates.permissions import (
    classify,
    matches,
    permissions_gate,
    record_permissions,
    take_snapshot,
)
from orchestrator.models.enums import BlockedReason, PhaseKind, PhaseName, WorkspaceKind
from orchestrator.trace import TraceWriter, verify_chain
from orchestrator.workspace.git import GitError, run_git

PROTECTED = tuple(sorted(set(constants.PROTECTED_PATHS_DEFAULT) | set(constants.LOCKFILES)))
BUILDER_WRITES = ("**",)
PLANNER_WRITES = ("specs/",)

SEED_FILES = {
    "src/app.py": "def run() -> int:\n    return 1\n",
    "tests/unit/test_a.py": "def test_a() -> None:\n    assert True\n",
    "tests/acceptance/test_holdout.py": "def test_holdout() -> None:\n    assert run() == 1\n",
    "pyproject.toml": "[project]\nname = 'demo'\n",
    "uv.lock": "version = 1\n",
    "CODEOWNERS": "* @acme/platform\n",
    ".github/workflows/ci.yml": "on: push\n",
    "specs/.gitkeep": "",
    "README.md": "# demo\n",
}


def write(repo: Path, rel: str, content: str) -> None:
    target = repo / Path(*rel.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "product"
    root.mkdir()
    run_git(["init", "-q", "-b", "main"], cwd=root)
    run_git(["config", "user.email", "factory@example.test"], cwd=root)
    run_git(["config", "user.name", "Factory Test"], cwd=root)
    run_git(["config", "core.autocrlf", "false"], cwd=root)
    for rel, content in SEED_FILES.items():
        write(root, rel, content)
    run_git(["add", "-A"], cwd=root)
    run_git(["commit", "-q", "-m", "seed"], cwd=root)
    return root


def test_matches_patterns() -> None:
    assert matches("anything/at/all.py", "**")
    assert matches("tests/acceptance/test_x.py", "tests/acceptance/")
    assert matches("tests/acceptance", "tests/acceptance/")
    assert not matches("tests/acceptance_extra/x.py", "tests/acceptance/")
    assert matches("CODEOWNERS", "CODEOWNERS")
    assert matches(".github/CODEOWNERS", "CODEOWNERS")
    assert matches("services/api/uv.lock", "uv.lock")
    assert not matches("src/CODEOWNERS.md", "CODEOWNERS")


def test_snapshot_sees_tracked_and_untracked(repo: Path) -> None:
    write(repo, "notes.txt", "untracked\n")
    snap = take_snapshot(repo)
    assert "notes.txt" in snap.files and "notes.txt" not in snap.tracked
    assert "src/app.py" in snap.tracked
    assert len(snap.head) == 40


def test_unchanged_workspace_passes(repo: Path) -> None:
    snap = take_snapshot(repo)
    outcome = permissions_gate(repo, snap, writes=BUILDER_WRITES, protected_paths=PROTECTED)
    assert outcome.result.passed
    assert [c.item for c in outcome.result.checks] == ["no changes"]
    assert outcome.blocked_reason is None


def test_seeded_holdout_edit_is_rolled_back_ten_of_ten(repo: Path) -> None:
    original = (repo / "tests/acceptance/test_holdout.py").read_text(encoding="utf-8")
    for _ in range(10):
        snap = take_snapshot(repo)
        write(repo, "tests/acceptance/test_holdout.py", "def test_holdout() -> None:\n    pass\n")
        write(repo, "src/app.py", "def run() -> int:\n    return 2\n")
        outcome = permissions_gate(repo, snap, writes=BUILDER_WRITES, protected_paths=PROTECTED)
        assert not outcome.result.passed
        assert [(v.path, v.kind) for v in outcome.violations] == [
            ("tests/acceptance/test_holdout.py", "protected_path")
        ]
        assert outcome.blocked_reason is BlockedReason.PROTECTED_PATH
        assert outcome.rolled_back == ("tests/acceptance/test_holdout.py",)
        assert (repo / "tests/acceptance/test_holdout.py").read_text(encoding="utf-8") == original
        # the allowed edit is left in place for the evidence
        assert "return 2" in (repo / "src/app.py").read_text(encoding="utf-8")
        write(repo, "src/app.py", SEED_FILES["src/app.py"])


def test_allowed_edit_and_new_unit_test_pass(repo: Path) -> None:
    snap = take_snapshot(repo)
    write(repo, "src/app.py", "def run() -> int:\n    return 3\n")
    write(repo, "tests/unit/test_b.py", "def test_b() -> None:\n    assert True\n")
    outcome = permissions_gate(repo, snap, writes=BUILDER_WRITES, protected_paths=PROTECTED)
    assert outcome.result.passed
    assert [(c.item, c.ok) for c in outcome.result.checks] == [
        ("modified src/app.py", True),
        ("added tests/unit/test_b.py", True),
    ]
    assert outcome.rolled_back == ()


def test_new_test_outside_allowed_dirs_is_removed(repo: Path) -> None:
    snap = take_snapshot(repo)
    write(repo, "tests/test_c.py", "def test_c() -> None:\n    assert True\n")
    outcome = permissions_gate(repo, snap, writes=BUILDER_WRITES, protected_paths=PROTECTED)
    assert [(v.path, v.kind) for v in outcome.violations] == [("tests/test_c.py", "test_location")]
    assert outcome.blocked_reason is BlockedReason.PROTECTED_PATH
    assert not (repo / "tests/test_c.py").exists()


@pytest.mark.parametrize("path", ["pyproject.toml", "uv.lock"])
def test_dependency_changes_block_for_a_human(repo: Path, path: str) -> None:
    snap = take_snapshot(repo)
    write(repo, path, "changed\n")
    outcome = permissions_gate(repo, snap, writes=BUILDER_WRITES, protected_paths=PROTECTED)
    assert [(v.path, v.kind) for v in outcome.violations] == [(path, "dependency_change")]
    assert outcome.blocked_reason is BlockedReason.DEPENDENCY_CHANGE
    assert (repo / path).read_text(encoding="utf-8") == SEED_FILES[path]


def test_deleting_codeowners_and_adding_a_workflow_are_restored(repo: Path) -> None:
    snap = take_snapshot(repo)
    (repo / "CODEOWNERS").unlink()
    write(repo, ".github/workflows/deploy.yml", "on: push\n")
    outcome = permissions_gate(repo, snap, writes=BUILDER_WRITES, protected_paths=PROTECTED)
    assert sorted((v.path, v.kind, v.change) for v in outcome.violations) == [
        (".github/workflows/deploy.yml", "protected_path", "added"),
        ("CODEOWNERS", "protected_path", "deleted"),
    ]
    assert (repo / "CODEOWNERS").read_text(encoding="utf-8") == SEED_FILES["CODEOWNERS"]
    assert not (repo / ".github/workflows/deploy.yml").exists()
    assert set(outcome.rolled_back) == {"CODEOWNERS", ".github/workflows/deploy.yml"}


def test_planner_boundary(repo: Path) -> None:
    snap = take_snapshot(repo)
    write(repo, "specs/418-invoice-due-date.md", "# Spec\n")
    outcome = permissions_gate(repo, snap, writes=PLANNER_WRITES, protected_paths=PROTECTED)
    assert outcome.result.passed

    snap = take_snapshot(repo)
    write(repo, "src/x.py", "x = 1\n")
    outcome = permissions_gate(repo, snap, writes=PLANNER_WRITES, protected_paths=PROTECTED)
    assert [(v.path, v.kind) for v in outcome.violations] == [("src/x.py", "outside_boundary")]
    assert outcome.blocked_reason is BlockedReason.PROTECTED_PATH
    assert not (repo / "src/x.py").exists()


def test_protected_wins_over_dependency_and_rollback_can_be_skipped(repo: Path) -> None:
    violations = classify(
        [Change("uv.lock", "modified"), Change("CODEOWNERS", "modified")],
        writes=BUILDER_WRITES,
        protected_paths=PROTECTED,
    )
    assert {v.kind for v in violations} == {"dependency_change", "protected_path"}
    snap = take_snapshot(repo)
    write(repo, "CODEOWNERS", "* @nobody\n")
    outcome = permissions_gate(
        repo, snap, writes=BUILDER_WRITES, protected_paths=PROTECTED, do_rollback=False
    )
    assert outcome.blocked_reason is BlockedReason.PROTECTED_PATH
    assert outcome.rolled_back == ()
    assert (repo / "CODEOWNERS").read_text(encoding="utf-8") == "* @nobody\n"


def test_record_permissions_writes_gate_and_incident(repo: Path, trace_writer: TraceWriter) -> None:
    task_id = "acme/atlas-api#418"
    trace_writer.insert_task(
        task_id=task_id,
        issue_number=418,
        title=None,
        branch="agent/418",
        workspace_kind=WorkspaceKind.WORKTREE,
        budget_usd=6.0,
        admitted_by="rsouza",
        config_sha="cfg1",
    )
    run_id = trace_writer.start_run(
        task_id=task_id, attempt=1, factory_version="0.1.0", config_sha="cfg1"
    )
    phase_id = trace_writer.start_phase(
        run_id=run_id,
        seq=1,
        name=PhaseName.CODE_GATES,
        kind=PhaseKind.CODE,
        owner="orchestrator",
        timeout_s=60,
    )
    ctx = GateContext(task_id=task_id, run_id=run_id, phase_id=phase_id)

    snap = take_snapshot(repo)
    write(repo, "uv.lock", "version = 2\n")
    dep = permissions_gate(repo, snap, writes=BUILDER_WRITES, protected_paths=PROTECTED)
    record_permissions(trace_writer, dep, ctx)

    snap = take_snapshot(repo)
    write(repo, "tests/acceptance/test_holdout.py", "pass\n")
    prot = permissions_gate(repo, snap, writes=BUILDER_WRITES, protected_paths=PROTECTED)
    gate_id = record_permissions(trace_writer, prot, ctx)

    kinds = [
        r["kind"]
        for r in trace_writer.db.fetchall(
            "SELECT kind FROM events WHERE phase_id = ? ORDER BY seq", (phase_id,)
        )
    ]
    assert kinds == ["gate_fail", "gate_fail", "incident"]
    incident = trace_writer.db.fetchone(
        "SELECT payload_json FROM events WHERE kind = 'incident' AND phase_id = ?", (phase_id,)
    )
    assert incident is not None
    payload = json.loads(incident["payload_json"])
    assert payload["paths"] == ["tests/acceptance/test_holdout.py"]
    assert payload["gate_results_id"] == gate_id
    rows = trace_writer.db.fetchall(
        "SELECT gate, passed FROM gate_results WHERE phase_id = ? ORDER BY created_at", (phase_id,)
    )
    assert [(r["gate"], r["passed"]) for r in rows] == [
        ("permissions_enforce", 0),
        ("permissions_enforce", 0),
    ]
    assert verify_chain(trace_writer.db).ok


def test_git_errors_are_named(tmp_path: Path) -> None:
    with pytest.raises(GitError, match="rev-parse"):
        run_git(["rev-parse", "HEAD"], cwd=tmp_path)
