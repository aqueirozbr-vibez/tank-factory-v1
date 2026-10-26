"""The between-phase budget check (TRD §6.3).

A phase does not start if the task's spend plus the phase cap would exceed
the task budget, or if today's spend plus the cap would exceed the daily cap.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from orchestrator.errors import TraceError
from orchestrator.trace.writer import TraceWriter


@dataclass(frozen=True)
class BudgetDecision:
    ok: bool
    reason: str | None
    task_spent: float
    task_budget: float
    phase_cap: float
    daily_spent: float
    daily_cap: float

    def payload(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "task_spent_usd": round(self.task_spent, 6),
            "task_budget_usd": self.task_budget,
            "phase_cap_usd": self.phase_cap,
            "daily_spent_usd": round(self.daily_spent, 6),
            "daily_cap_usd": self.daily_cap,
        }


def day_start_utc(now: datetime, timezone: str) -> str:
    """Midnight of ``now``'s day in the installation time zone, as a UTC ISO timestamp."""
    local = now.astimezone(ZoneInfo(timezone))
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(UTC).isoformat(timespec="milliseconds")


def check_budget(
    writer: TraceWriter, *, task_id: str, phase_cap_usd: float, now: datetime | None = None
) -> BudgetDecision:
    task = writer.get_task(task_id)
    if task is None:
        raise TraceError(f"task {task_id} is not admitted")
    installation = writer.get_installation()
    if installation is None:
        raise TraceError("the installation row is missing")
    moment = now or datetime.now(UTC)
    task_spent = float(task["spent_usd"])
    task_budget = float(task["budget_usd"])
    daily_spent = writer.spent_since(day_start_utc(moment, str(installation["timezone"])))
    daily_cap = float(installation["daily_cap_usd"])
    reason: str | None = None
    if task_spent + phase_cap_usd > task_budget:
        reason = (
            f"task budget: spent {task_spent:.2f} + phase cap {phase_cap_usd:.2f}"
            f" exceeds {task_budget:.2f}"
        )
    elif daily_spent + phase_cap_usd > daily_cap:
        reason = (
            f"daily cap: spent today {daily_spent:.2f} + phase cap {phase_cap_usd:.2f}"
            f" exceeds {daily_cap:.2f}"
        )
    return BudgetDecision(
        ok=reason is None,
        reason=reason,
        task_spent=task_spent,
        task_budget=task_budget,
        phase_cap=phase_cap_usd,
        daily_spent=daily_spent,
        daily_cap=daily_cap,
    )
