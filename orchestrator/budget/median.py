"""The budget switch to the rolling median (PRD §Capability 5 · Budgets; TRD §4.2 ``budget_mode``).

For the first week after install the per-task budget is the install
derivation (2x the highest dry-run cost). From day 8 the budget is 2x the
median cost per task over the trailing 30 days, or over every task so far
while fewer than 30 days exist, recomputed once a day; the daily cap
follows by the ``installation_daily_cap`` trigger. The PRD names no floor
and no ceiling, so none is applied here (open question in the tracker).
"""

import asyncio
import statistics
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from orchestrator import constants
from orchestrator.clock import parse_ts
from orchestrator.errors import TraceError
from orchestrator.models.enums import EventKind
from orchestrator.trace.writer import TraceWriter

MEASURED_STATUSES = ("pr_open", "merged", "blocked", "cancelled")  # a run that is over
MedianCode = Literal["install_period", "no_measurements", "switched", "recomputed", "unchanged"]
Clock = Callable[[], datetime]


def utc_clock() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class MedianOutcome:
    code: MedianCode
    day: str
    per_task_budget_usd: float
    median_usd: float | None = None
    tasks: int = 0
    previous_usd: float | None = None
    event_seq: int | None = None

    @property
    def changed(self) -> bool:
        return self.code in ("switched", "recomputed")


def measured_costs(writer: TraceWriter, *, since: str) -> list[float]:
    """Spend per task admitted since ``since`` whose run is over; a zero spend is no measurement."""
    rows = writer.db.fetchall(
        "SELECT spent_usd FROM tasks WHERE created_at >= ? AND spent_usd > 0"
        " AND status IN (?, ?, ?, ?) ORDER BY created_at",
        (since, *MEASURED_STATUSES),
    )
    return [float(r["spent_usd"]) for r in rows]


class MedianBudget:
    def __init__(self, writer: TraceWriter, *, clock: Clock = utc_clock) -> None:
        self.writer = writer
        self.clock = clock
        self.last_day: str | None = None

    def refresh(self, *, now: datetime | None = None) -> MedianOutcome:
        """Apply the rule once for ``now``; writes only when the budget moves."""
        installation = self.writer.get_installation()
        if installation is None:
            raise TraceError("the installation row is missing")
        moment = now or self.clock()
        day = _local_day(moment, str(installation["timezone"]))
        current = float(installation["per_task_budget_usd"])
        installed = parse_ts(str(installation["created_at"]))
        if moment - installed < timedelta(days=constants.BUDGET_INSTALL_PERIOD_DAYS):
            return MedianOutcome("install_period", day, current)
        window = timedelta(days=constants.BUDGET_MEDIAN_WINDOW_DAYS)
        since = (moment - window).isoformat(timespec="milliseconds")
        costs = measured_costs(self.writer, since=since)
        if not costs:
            return MedianOutcome("no_measurements", day, current)
        median = statistics.median(costs)
        new = round(constants.MEDIAN_BUDGET_FACTOR * median, 4)
        mode = str(installation["budget_mode"])
        if mode == "median" and abs(new - current) < 1e-9:
            return MedianOutcome("unchanged", day, current, median, len(costs))
        self.writer.set_per_task_budget(new, mode="median")
        seq = self.writer.write_event(
            EventKind.CONTROL,
            "orchestrator",
            payload={
                "action": "budget_median",
                "day": day,
                "old": current,
                "new": new,
                "median_usd": round(median, 6),
                "tasks": len(costs),
                "window_days": constants.BUDGET_MEDIAN_WINDOW_DAYS,
                "mode_before": mode,
            },
        )
        code: MedianCode = "switched" if mode != "median" else "recomputed"
        return MedianOutcome(code, day, new, median, len(costs), current, seq)

    async def run(self, stop: asyncio.Event, *, interval_s: float = 60.0) -> None:
        """Refresh at start and on every local day change (the daily recomputation)."""
        while not stop.is_set():
            if self._today() != self.last_day:
                self.last_day = self.refresh().day
            try:
                await asyncio.wait_for(stop.wait(), interval_s)
            except TimeoutError:
                continue

    def _today(self) -> str:
        installation = self.writer.get_installation()
        timezone = str(installation["timezone"]) if installation is not None else "UTC"
        return _local_day(self.clock(), timezone)


def _local_day(moment: datetime, timezone: str) -> str:
    return moment.astimezone(ZoneInfo(timezone)).date().isoformat()


__all__ = ["MEASURED_STATUSES", "MedianBudget", "MedianOutcome", "measured_costs"]
