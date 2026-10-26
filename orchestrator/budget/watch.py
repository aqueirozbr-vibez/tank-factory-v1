"""The spend watchdog: the proxy's spend during a phase ends it at a cap (TRD §6.3).

Every ``SPEND_WATCHDOG_INTERVAL_S`` the guard reads the phase key's spend
from the proxy and projects the task's and today's totals; at the task
budget or the daily cap it writes ``budget_stop``, cancels the harness call
(which terminates the process tree and, in a container, the container) and
the phase reports ``error_max_budget_usd``, so the task blocks for budget
within one interval plus the SIGTERM grace.
"""

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from orchestrator import constants
from orchestrator.budget.check import day_start_utc
from orchestrator.budget.proxy import ProxyAdmin, ProxyError
from orchestrator.harness.base import (
    HarnessAdapter,
    HarnessRequest,
    HarnessResult,
    ProcessSink,
    ToolCallSink,
)
from orchestrator.models.enums import EventKind
from orchestrator.trace.writer import TraceWriter

Clock = Callable[[], datetime]


def utc_clock() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class SpendBreach:
    cap: Literal["task", "daily"]
    phase_spend: float
    task_spent: float
    task_budget: float
    daily_spent: float
    daily_cap: float
    phase_cap: float

    def payload(self) -> dict[str, object]:
        return {
            "cap": self.cap,
            "phase_spend_usd": round(self.phase_spend, 6),
            "task_spent_usd": round(self.task_spent, 6),
            "task_budget_usd": self.task_budget,
            "phase_cap_usd": self.phase_cap,
            "daily_spent_usd": round(self.daily_spent, 6),
            "daily_cap_usd": self.daily_cap,
        }

    def error(self) -> str:
        return (
            f"error_max_budget_usd: {self.cap} cap reached during the phase"
            f" (phase spend {self.phase_spend:.4f}; task {self.task_spent:.2f} of"
            f" {self.task_budget:.2f}; today {self.daily_spent:.2f} of {self.daily_cap:.2f})"
        )


class SpendGuard:
    """One phase's watchdog: the key, the rows it projects against, and the breach it found."""

    def __init__(
        self,
        *,
        admin: ProxyAdmin,
        key: str,
        writer: TraceWriter,
        task_id: str,
        run_id: str,
        phase_id: str,
        phase_cap_usd: float,
        interval_s: float = constants.SPEND_WATCHDOG_INTERVAL_S,
        clock: Clock = utc_clock,
    ) -> None:
        self.admin = admin
        self.key = key
        self.writer = writer
        self.task_id = task_id
        self.run_id = run_id
        self.phase_id = phase_id
        self.phase_cap_usd = phase_cap_usd
        self.interval_s = interval_s
        self.clock = clock
        self.breach: SpendBreach | None = None
        self.reads = 0

    def project(self, phase_spend: float) -> SpendBreach | None:
        """The task's and today's totals with this phase's spend added; the first cap crossed."""
        task = self.writer.get_task(self.task_id)
        installation = self.writer.get_installation()
        if task is None or installation is None:
            return None
        task_spent = float(task["spent_usd"]) + phase_spend
        task_budget = float(task["budget_usd"])
        start = day_start_utc(self.clock(), str(installation["timezone"]))
        daily_spent = self.writer.spent_since(start) + phase_spend
        daily_cap = float(installation["daily_cap_usd"])
        cap: Literal["task", "daily"] | None = None
        if task_spent >= task_budget - 1e-9:
            cap = "task"
        elif daily_spent >= daily_cap - 1e-9:
            cap = "daily"
        if cap is None:
            return None
        return SpendBreach(
            cap, phase_spend, task_spent, task_budget, daily_spent, daily_cap, self.phase_cap_usd
        )

    async def watch(self, victim: "asyncio.Future[HarnessResult]") -> None:
        while not victim.done():
            await asyncio.sleep(self.interval_s)
            try:
                spend = (await self.admin.key_info(self.key)).spend
            except ProxyError:
                continue  # a proxy hiccup is not a breach; the next read decides
            self.reads += 1
            breach = self.project(spend)
            if breach is None:
                continue
            self.breach = breach
            self.writer.write_event(
                EventKind.BUDGET_STOP,
                "orchestrator",
                task_id=self.task_id,
                run_id=self.run_id,
                phase_id=self.phase_id,
                payload=breach.payload(),
            )
            victim.cancel("budget")
            return


async def run_guarded(
    harness: HarnessAdapter,
    request: HarnessRequest,
    *,
    on_tool_call: ToolCallSink,
    on_process: ProcessSink | None,
    guard: SpendGuard | None,
) -> HarnessResult:
    """``harness.run`` under the spend watchdog; a breach ends the call and names the cap."""
    if guard is None:
        return await harness.run(request, on_tool_call=on_tool_call, on_process=on_process)
    victim = asyncio.ensure_future(
        harness.run(request, on_tool_call=on_tool_call, on_process=on_process)
    )
    watcher = asyncio.ensure_future(guard.watch(victim))
    try:
        return await victim
    except asyncio.CancelledError:
        if guard.breach is None:
            raise  # the phase itself was cancelled (kill-switch, label): let it unwind
        return HarnessResult(
            harness=harness.name,
            harness_version=harness.version(),
            outcome="killed",
            error=guard.breach.error(),
        )
    finally:
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await watcher
