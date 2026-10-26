"""Live tasks as asyncio tasks: start, cancel one, cancel all (PRD §Capability 1, 5)."""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

Runner = Callable[[str], Awaitable[None]]
Finished = Callable[[str], None]


class Supervisor:
    def __init__(self, runner: Runner, *, on_finished: Finished | None = None) -> None:
        self._runner = runner
        self._on_finished = on_finished
        self._tasks: dict[str, asyncio.Task[None]] = {}

    @property
    def live(self) -> list[str]:
        return sorted(self._tasks)

    def is_live(self, task_id: str) -> bool:
        return task_id in self._tasks

    def start(self, task_id: str) -> None:
        if task_id in self._tasks:
            return
        self._tasks[task_id] = asyncio.create_task(self._wrap(task_id), name=f"task:{task_id}")

    async def _wrap(self, task_id: str) -> None:
        try:
            await self._runner(task_id)
        finally:
            self._tasks.pop(task_id, None)
            if self._on_finished is not None:
                self._on_finished(task_id)

    async def cancel(self, task_id: str, *, reason: str) -> bool:
        """Cancel a live task and wait for it to unwind; ``False`` when it was not live."""
        task = self._tasks.get(task_id)
        if task is None:
            return False
        task.cancel(reason)
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        return True

    async def cancel_all(self, *, reason: str) -> list[str]:
        cancelled: list[str] = []
        for task_id in self.live:
            if await self.cancel(task_id, reason=reason):
                cancelled.append(task_id)
        return cancelled

    async def wait(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
