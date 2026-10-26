"""Claim gates: every claim in an envelope is checked by code (PRD §Capability 3)."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from orchestrator.gates.base import Check, GateResult, Violation
from orchestrator.gates.permissions import PathChange
from orchestrator.models.enums import GateName


def _file(worktree: Path, rel: str) -> Path:
    return worktree / Path(*rel.split("/"))


def artifacts_exist(worktree: Path, paths: Sequence[str]) -> GateResult:
    checks = [
        Check(
            p, _file(worktree, p).is_file(), "exists" if _file(worktree, p).is_file() else "missing"
        )
        for p in paths
    ]
    violations = [Violation(c.item, "claimed file does not exist") for c in checks if not c.ok]
    return GateResult(
        GateName.ARTIFACTS_EXIST,
        not violations,
        tuple(checks) or (Check("no artifacts claimed", True),),
        tuple(violations),
    )


def _non_empty(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    try:
        return bool(path.read_text(encoding="utf-8").strip())
    except UnicodeDecodeError:
        return True  # binary content counts as content


def files_non_empty(worktree: Path, paths: Sequence[str]) -> GateResult:
    checks = []
    for p in paths:
        ok = _non_empty(_file(worktree, p))
        checks.append(Check(p, ok, "has content" if ok else "empty or missing"))
    violations = [Violation(c.item, "claimed file is empty") for c in checks if not c.ok]
    return GateResult(
        GateName.FILES_NON_EMPTY,
        not violations,
        tuple(checks) or (Check("no files claimed", True),),
        tuple(violations),
    )


def diff_matches_claims(changes: Sequence[PathChange], claimed: Sequence[str]) -> GateResult:
    changed = {c.path: c.change for c in changes}
    checks = [Check(p, p in changed, changed.get(p, "not in git status")) for p in claimed]
    for path, change in changed.items():
        if path not in claimed:
            checks.append(Check(path, True, f"{change} but not claimed"))
    violations = [
        Violation(c.item, "claimed as changed but git status shows no change")
        for c in checks
        if not c.ok
    ]
    return GateResult(
        GateName.DIFF_MATCHES_CLAIMS,
        not violations,
        tuple(checks) or (Check("no changes claimed", True),),
        tuple(violations),
    )


@dataclass(frozen=True)
class ClaimOutcome:
    results: tuple[GateResult, ...]

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def first_failure(self) -> GateResult | None:
        return next((r for r in self.results if not r.passed), None)


def claim_gates(
    worktree: Path, changes: Sequence[PathChange], claimed: Sequence[str]
) -> ClaimOutcome:
    """The three claim gates in the PRD's order, all run so the evidence is complete."""
    unique = list(dict.fromkeys(claimed))
    return ClaimOutcome(
        (
            artifacts_exist(worktree, unique),
            files_non_empty(worktree, unique),
            diff_matches_claims(changes, unique),
        )
    )
