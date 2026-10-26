"""N slots, label order (PRD §Capability 1 · concurrency)."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from orchestrator.budget.check import day_start_utc
from orchestrator.models.enums import EventKind, TaskStatus
from orchestrator.supervisor import Supervisor
from orchestrator.trace.writer import TraceWriter


class Scheduler:
    def __init__(
        self,
        writer: TraceWriter,
        supervisor: Supervisor,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.writer = writer
        self.supervisor = supervisor
        self.clock = clock or (lambda: datetime.now(UTC))
        self._pending: set[asyncio.Task[Any]] = set()
        self._held_day: str | None = None

    def daily_cap_reached(self) -> tuple[bool, str, float]:
        """Whether today's spend has reached the daily cap (TRD §6.3: the queue holds)."""
        installation = self.writer.get_installation()
        if installation is None:
            return False, "", 0.0
        start = day_start_utc(self.clock(), str(installation["timezone"]))
        cap = float(installation["daily_cap_usd"])
        return self.writer.spent_since(start) >= cap - 1e-9, start, cap

    def concurrency(self) -> int:
        installation = self.writer.get_installation()
        return int(installation["concurrency_n"]) if installation else 1

    def slots_free(self) -> int:
        return max(0, self.concurrency() - self.writer.count_running())

    async def dispatch(self) -> list[str]:
        """Promote queued tasks into the free slots, oldest first; return the started ids."""
        started: list[str] = []
        if not self.writer.factory_enabled():
            return started  # the kill-switch: nothing starts while the flag is 0
        if self.writer.admissions_paused():
            return started  # an upgrade or the backlog throttle holds the queue (TRD §11, §6.4)
        reached, day_start, cap = self.daily_cap_reached()
        if reached:
            if self._held_day != day_start:  # one log per day, not one per dispatch
                self._held_day = day_start
                self.writer.write_event(
                    EventKind.LOG,
                    "orchestrator",
                    payload={
                        "scheduler": "daily_cap_hold",
                        "day_start": day_start,
                        "daily_cap_usd": cap,
                    },
                )
            return started
        while self.slots_free() > 0:
            task_id = self.writer.oldest_queued()
            if task_id is None:
                break
            self.writer.set_task_status(task_id, TaskStatus.RUNNING)
            self.writer.write_event(
                EventKind.LOG,
                "orchestrator",
                task_id=task_id,
                payload={"scheduler": "started", "slots_free_after": self.slots_free()},
            )
            self.supervisor.start(task_id)
            started.append(task_id)
        return started

    async def settle(self) -> None:
        """Wait for dispatches scheduled by finished tasks (shutdown and tests)."""
        while self._pending:
            await asyncio.gather(*list(self._pending), return_exceptions=True)

    def on_finished(self, task_id: str) -> None:
        """Called by the supervisor when a task ends; promotes the next queued task."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self.dispatch(), name=f"dispatch-after:{task_id}")
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)
