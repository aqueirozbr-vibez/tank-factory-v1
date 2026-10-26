"""The install-time dry run and the install log (PRD §Capability 5 · Budgets; TRD §4.4).

Three issues are replayed; the per-task budget is twice the highest cost
among the ones that completed. Fewer than two completions refuse to go
live, and so does a dry run that observed no cost. Every derivation is one
line in the install log on the data volume.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from orchestrator import constants
from orchestrator.clock import utc_now

INSTALL_LOG = "install.log"


@dataclass(frozen=True)
class DryRunIssue:
    issue: int
    completed: bool
    cost_usd: float
    blocked_reason: str | None = None

    def record(self) -> dict[str, object]:
        return {
            "issue": self.issue,
            "completed": self.completed,
            "cost_usd": round(self.cost_usd, 4),
            "blocked_reason": self.blocked_reason,
        }


@dataclass(frozen=True)
class Derivation:
    issues: tuple[DryRunIssue, ...]
    completed: int
    highest_cost_usd: float | None
    per_task_budget_usd: float | None
    reason: str
    floor_usd: float | None = None  # the industry per-task cap the budget never drops below

    @property
    def refused(self) -> bool:
        return self.per_task_budget_usd is None

    def record(self) -> dict[str, object]:
        return {
            "kind": "dry_run",
            "issues": [i.record() for i in self.issues],
            "completed": self.completed,
            "required": constants.DRY_RUN_MIN_COMPLETE,
            "highest_cost_usd": self.highest_cost_usd,
            "factor": constants.INSTALL_BUDGET_FACTOR,
            "per_task_budget_usd": self.per_task_budget_usd,
            "refused": self.refused,
            "reason": self.reason,
            "floor_usd": self.floor_usd,
        }


def derive_budget(issues: Sequence[DryRunIssue]) -> Derivation:
    """2x the highest cost among completed issues, never below the industry per-task cap;
    refuse below two completions or at zero (user decision 2026-10-25, spec 67)."""
    done = [i for i in issues if i.completed]
    if len(done) < constants.DRY_RUN_MIN_COMPLETE:
        return Derivation(
            tuple(issues),
            len(done),
            None,
            None,
            f"only {len(done)} of {len(issues)} dry-run issues completed; the installer refuses"
            f" to go live below {constants.DRY_RUN_MIN_COMPLETE}",
        )
    highest = max(i.cost_usd for i in done)
    budget = round(highest * constants.INSTALL_BUDGET_FACTOR, 2)
    if budget <= 0:
        return Derivation(
            tuple(issues),
            len(done),
            highest,
            None,
            "the dry run observed no cost; a budget cannot derive from zero",
        )
    floor = constants.INDUSTRY_PER_TASK_CAP_USD
    if budget < floor:
        return Derivation(
            tuple(issues),
            len(done),
            highest,
            floor,
            f"the industry per-task cap {floor:.2f};"
            f" {constants.INSTALL_BUDGET_FACTOR:g}x the highest observed cost {highest:.2f}"
            f" ({budget:.2f}) sits below it",
            floor_usd=floor,
        )
    return Derivation(
        tuple(issues),
        len(done),
        highest,
        budget,
        f"{constants.INSTALL_BUDGET_FACTOR:g}x the highest observed cost {highest:.2f}",
        floor_usd=floor,
    )


class InstallLog:
    """Append-only JSON lines on the data volume: derivations, self-tests, the public key."""

    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / INSTALL_LOG

    def append(self, record: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"at": utc_now(), **record}, sort_keys=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")

    def records(self) -> list[dict[str, object]]:
        if not self.path.is_file():
            return []
        rows: list[dict[str, object]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                loaded = json.loads(line)
                if isinstance(loaded, dict):
                    rows.append(loaded)
        return rows
