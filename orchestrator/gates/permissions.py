"""``permissions_enforce``: the write boundary checked by snapshot and diff.

Hooks deny what they can see; this gate sees everything that changed and
rolls the forbidden part back (PRD §Capability 3 · Write boundary).
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from orchestrator import constants
from orchestrator.gates.base import Check, GateContext, GateResult, Violation, write_gate
from orchestrator.models.enums import BlockedReason, EventKind, GateName
from orchestrator.trace.writer import TraceWriter
from orchestrator.workspace.git import hash_files, head_sha, list_files, restore_tracked

ViolationKind = Literal["protected_path", "dependency_change", "outside_boundary", "test_location"]
ChangeKind = Literal["added", "modified", "deleted"]


@dataclass(frozen=True)
class Snapshot:
    head: str
    files: Mapping[str, str]
    tracked: frozenset[str]


@dataclass(frozen=True)
class PathChange:
    path: str
    change: ChangeKind


@dataclass(frozen=True)
class PathViolation:
    path: str
    kind: ViolationKind
    change: ChangeKind


@dataclass(frozen=True)
class PermissionsOutcome:
    result: GateResult
    changes: tuple[PathChange, ...]
    violations: tuple[PathViolation, ...]
    rolled_back: tuple[str, ...]

    @property
    def blocked_reason(self) -> BlockedReason | None:
        kinds = {v.kind for v in self.violations}
        if kinds & {"protected_path", "outside_boundary", "test_location"}:
            return BlockedReason.PROTECTED_PATH
        if "dependency_change" in kinds:
            return BlockedReason.DEPENDENCY_CHANGE
        return None

    @property
    def touched_protected(self) -> bool:
        return any(v.kind == "protected_path" for v in self.violations)


# --- snapshot and diff --------------------------------------------------------


def take_snapshot(worktree: Path) -> Snapshot:
    tracked, untracked = list_files(worktree)
    files = hash_files(worktree, [*tracked, *untracked])
    return Snapshot(head=head_sha(worktree), files=files, tracked=frozenset(tracked))


def diff_since(worktree: Path, before: Snapshot) -> list[PathChange]:
    tracked, untracked = list_files(worktree)
    after = hash_files(worktree, [*tracked, *untracked])
    changes: list[PathChange] = []
    for path in sorted(set(before.files) | set(after)):
        old, new = before.files.get(path), after.get(path)
        if old == new:
            continue
        if old is None:
            changes.append(PathChange(path, "added"))
        elif new is None:
            changes.append(PathChange(path, "deleted"))
        else:
            changes.append(PathChange(path, "modified"))
    return changes


# --- classification -------------------------------------------------------------


def matches(path: str, pattern: str) -> bool:
    """``**`` matches all; a trailing ``/`` is a prefix; otherwise exact path or basename."""
    if pattern == "**":
        return True
    if pattern.endswith("/"):
        return path.startswith(pattern) or path == pattern.rstrip("/")
    return path == pattern or path.rsplit("/", 1)[-1] == pattern


def classify(
    changes: Iterable[PathChange],
    *,
    writes: Sequence[str],
    protected_paths: Sequence[str],
    manifests: Sequence[str] = constants.DEPENDENCY_MANIFESTS,
    lockfiles: Sequence[str] = constants.LOCKFILES,
    test_dirs: Sequence[str] = constants.BUILDER_TEST_DIRS,
) -> list[PathViolation]:
    violations: list[PathViolation] = []
    for change in changes:
        path = change.path
        if any(matches(path, m) for m in (*manifests, *lockfiles)):
            violations.append(PathViolation(path, "dependency_change", change.change))
        elif any(matches(path, p) for p in protected_paths):
            violations.append(PathViolation(path, "protected_path", change.change))
        elif not any(matches(path, w) for w in writes):
            violations.append(PathViolation(path, "outside_boundary", change.change))
        elif (
            change.change == "added"
            and path.startswith("tests/")
            and not any(path.startswith(d) for d in test_dirs)
        ):
            violations.append(PathViolation(path, "test_location", change.change))
    return violations


# --- rollback and the gate --------------------------------------------------------


def rollback(worktree: Path, before: Snapshot, violations: Sequence[PathViolation]) -> list[str]:
    """Restore violating tracked paths from the index and delete violating additions."""
    restored: list[str] = []
    tracked = [v.path for v in violations if v.path in before.tracked]
    if tracked:
        restore_tracked(worktree, tracked)
        restored.extend(tracked)
    for violation in violations:
        if violation.path in before.tracked:
            continue
        target = worktree / Path(*violation.path.split("/"))
        if target.is_file():
            target.unlink()
            restored.append(violation.path)
    return restored


def permissions_gate(
    worktree: Path,
    before: Snapshot,
    *,
    writes: Sequence[str],
    protected_paths: Sequence[str],
    manifests: Sequence[str] = constants.DEPENDENCY_MANIFESTS,
    lockfiles: Sequence[str] = constants.LOCKFILES,
    test_dirs: Sequence[str] = constants.BUILDER_TEST_DIRS,
    do_rollback: bool = True,
) -> PermissionsOutcome:
    changes = diff_since(worktree, before)
    violations = classify(
        changes,
        writes=writes,
        protected_paths=protected_paths,
        manifests=manifests,
        lockfiles=lockfiles,
        test_dirs=test_dirs,
    )
    by_path = {v.path: v for v in violations}
    checks = [
        Check(
            f"{c.change} {c.path}",
            c.path not in by_path,
            by_path[c.path].kind if c.path in by_path else "inside boundary",
        )
        for c in changes
    ] or [Check("no changes", True)]
    rolled_back = rollback(worktree, before, violations) if violations and do_rollback else []
    result = GateResult(
        gate=GateName.PERMISSIONS_ENFORCE,
        passed=not violations,
        checks=tuple(checks),
        violations=tuple(
            Violation(v.path, f"{v.kind}: {v.change} is not allowed here") for v in violations
        ),
    )
    return PermissionsOutcome(result, tuple(changes), tuple(violations), tuple(rolled_back))


def record_permissions(writer: TraceWriter, outcome: PermissionsOutcome, ctx: GateContext) -> str:
    gate_id = write_gate(writer, outcome.result, ctx)
    if outcome.touched_protected:
        writer.write_event(
            EventKind.INCIDENT,
            ctx.actor,
            task_id=ctx.task_id,
            run_id=ctx.run_id,
            phase_id=ctx.phase_id,
            payload={
                "kind": "protected_path_attempt",
                "paths": [v.path for v in outcome.violations if v.kind == "protected_path"],
                "rolled_back": list(outcome.rolled_back),
                "gate_results_id": gate_id,
            },
        )
    return gate_id
