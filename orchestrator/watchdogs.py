"""Watchdogs: the stuck-run flag (PRD §Capability 5 · Stuck runs; TRD §6.4).

The phase timeout kills; this only notices. A running process with no event
for 15 minutes is flagged once with a ``log`` event the console and Slack
read.
"""

import asyncio
import contextlib
import re
import subprocess
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from orchestrator import constants
from orchestrator.clock import parse_ts
from orchestrator.intake import messages
from orchestrator.models.enums import EventKind, TaskStatus
from orchestrator.supervisor import Supervisor
from orchestrator.trace.db import TraceDB
from orchestrator.trace.writer import TraceWriter
from orchestrator.workspace.container import DockerCli


@dataclass(frozen=True)
class StuckProcess:
    id: str
    run_id: str
    phase_id: str | None
    task_id: str
    name: str
    last_event_at: str
    idle_s: float


OnStuck = Callable[[StuckProcess], Awaitable[None]]  # the Slack notice (unit 50)


def find_stuck(
    db: TraceDB, *, now: datetime | None = None, threshold_s: int = constants.STUCK_AFTER_S
) -> list[StuckProcess]:
    moment = now or datetime.now(UTC)
    rows = db.fetchall(
        "SELECT p.id, p.run_id, p.phase_id, r.task_id, p.name, p.last_event_at"
        " FROM processes p JOIN runs r ON r.run_id = p.run_id"
        " WHERE p.ended_at IS NULL AND p.last_event_at IS NOT NULL ORDER BY p.started_at"
    )
    stuck: list[StuckProcess] = []
    for row in rows:
        idle = (moment - parse_ts(str(row["last_event_at"]))).total_seconds()
        if idle > threshold_s:
            stuck.append(
                StuckProcess(
                    id=str(row["id"]),
                    run_id=str(row["run_id"]),
                    phase_id=row["phase_id"],
                    task_id=str(row["task_id"]),
                    name=str(row["name"]),
                    last_event_at=str(row["last_event_at"]),
                    idle_s=idle,
                )
            )
    return stuck


class Watchdog:
    """Flags stuck processes once; ``run`` ticks until ``stop`` is set."""

    def __init__(
        self, writer: TraceWriter, *, interval_s: float = 30.0, on_stuck: OnStuck | None = None
    ) -> None:
        self.writer = writer
        self.interval_s = interval_s
        self.on_stuck = on_stuck
        self.flagged: set[str] = set()

    def tick(self, *, now: datetime | None = None) -> list[StuckProcess]:
        newly: list[StuckProcess] = []
        for process in find_stuck(self.writer.db, now=now):
            if process.id in self.flagged:
                continue
            self.flagged.add(process.id)
            self.writer.write_event(
                EventKind.LOG,
                "orchestrator",
                task_id=process.task_id,
                run_id=process.run_id,
                phase_id=process.phase_id,
                payload={
                    "stuck": True,
                    "process_id": process.id,
                    "name": process.name,
                    "last_event_at": process.last_event_at,
                    "idle_s": int(process.idle_s),
                    "threshold_s": constants.STUCK_AFTER_S,
                },
            )
            newly.append(process)
        return newly

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            for process in self.tick():
                if self.on_stuck is not None:
                    await self.on_stuck(process)
            try:
                await asyncio.wait_for(stop.wait(), self.interval_s)
            except TimeoutError:
                continue


def sweep_on_start(writer: TraceWriter) -> list[str]:
    """Close work a previous process left open and requeue running tasks (TRD §7.8)."""
    rows = writer.db.fetchall(
        "SELECT task_id FROM tasks WHERE status = 'running' ORDER BY created_at, task_id"
    )
    requeued: list[str] = []
    for row in rows:
        task_id = str(row["task_id"])
        last = writer.last_completed_phase(task_id)
        writer.close_open_work(task_id, error="restart")
        writer.requeue_task(task_id)
        writer.write_event(
            EventKind.CONTROL,
            "orchestrator",
            task_id=task_id,
            payload={"action": "restart_requeue", "last_completed_phase": last},
        )
        requeued.append(task_id)
    orphans = writer.end_orphan_processes()
    if orphans:
        writer.write_event(
            EventKind.LOG, "orchestrator", payload={"sweep": "orphan_processes", "ended": orphans}
        )
    return requeued


# --- egress (TRD §3.2): the VM's nftables rules and the denial tailer --------------------

EGRESS_PREFIX = "factory-egress"
KV = re.compile(r"\b([A-Z]+)=(\S*)")


def nftables_rules(bridge: str, proxy_ip: str, port: int = 4000) -> str:
    """Host rules: the task bridge reaches only the proxy; the rest is logged and dropped."""
    return (
        "#!/usr/sbin/nft -f\n"
        "# Rendered by orchestrator.watchdogs.nftables_rules; applied on the VM host.\n"
        "table inet factory {\n"
        "  chain docker-user {\n"
        "    type filter hook forward priority -10; policy accept;\n"
        f'    iifname "{bridge}" ct state established,related accept\n'
        f'    iifname "{bridge}" ip daddr {proxy_ip} tcp dport {port} accept\n'
        f'    iifname "{bridge}" log prefix "{EGRESS_PREFIX} " level warn\n'
        f'    iifname "{bridge}" drop\n'
        "  }\n"
        "}\n"
    )


@dataclass(frozen=True)
class Denial:
    src: str
    dst: str
    proto: str | None
    dport: int | None
    raw: str


def parse_denial(line: str) -> Denial | None:
    if EGRESS_PREFIX not in line:
        return None
    fields = dict(KV.findall(line))
    src, dst = fields.get("SRC"), fields.get("DST")
    if not src or not dst:
        return None
    dport = fields.get("DPT")
    return Denial(
        src=src,
        dst=dst,
        proto=fields.get("PROTO"),
        dport=int(dport) if dport and dport.isdigit() else None,
        raw=line[:300],
    )


class EgressTailer:
    """Kernel log lines in, ``egress_denied`` events out; the source IP names the task."""

    def __init__(self, writer: TraceWriter, docker: DockerCli, *, network: str) -> None:
        self.writer = writer
        self.docker = docker
        self.network = network
        self.denials = 0

    async def feed(self, line: str) -> bool:
        denial = parse_denial(line)
        if denial is None:
            return False
        owners = await self.docker.task_containers_by_ip(self.network)
        container, task_id = owners.get(denial.src, (None, None))
        self.denials += 1
        self.writer.write_event(
            EventKind.EGRESS_DENIED,
            "orchestrator",
            task_id=task_id,
            payload={
                "container": container,
                "src": denial.src,
                "dst": denial.dst,
                "dport": denial.dport,
                "proto": denial.proto,
                "line": denial.raw,
            },
        )
        return True

    async def run(self, lines: AsyncIterator[str], stop: asyncio.Event) -> None:
        async for line in lines:
            if stop.is_set():
                break
            await self.feed(line)


async def journal_lines(
    stop: asyncio.Event, *, journalctl: str = "journalctl"
) -> AsyncIterator[str]:
    """``journalctl -k -f`` as a line stream (the VM only)."""
    proc = await asyncio.create_subprocess_exec(
        journalctl,
        "-k",
        "-f",
        "-n",
        "0",
        "-o",
        "cat",
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert proc.stdout is not None
    try:
        while not stop.is_set():
            raw = await proc.stdout.readline()
            if not raw:
                break
            yield raw.decode("utf-8", "replace").rstrip()
    finally:
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()


# --- kill-switch (PRD §Capability 5 · Kill-switch, Re-enabling; TRD §6.1, §7.6) -----------


class Commenter(Protocol):
    async def create_comment(self, number: int, body: str, *, task_id: str) -> object: ...


@dataclass(frozen=True)
class StopReport:
    enabled: bool
    event_seq: int | None
    cancelled: tuple[str, ...]
    containers: tuple[str, ...]
    live_after: int
    elapsed_s: float


class KillSwitch:
    """``installation.factory_enabled``: its fan-out and the re-enable control.

    ``disable`` writes the flag, cancels every live task through the supervisor
    (the runner's own cancellation terminates the process tree and removes the
    container), fails the open rows, marks the tasks cancelled, posts the
    comment, kills any container still labelled ``factory.task`` and writes one
    ``control`` event carrying the report. ``run`` re-reads the flag so a flag
    another process wrote is honoured within the bound.
    """

    def __init__(
        self,
        writer: TraceWriter,
        *,
        supervisor: Supervisor | None = None,
        docker: DockerCli | None = None,
        github: Commenter | None = None,
        interval_s: float = constants.KILL_SWITCH_POLL_S,
    ) -> None:
        self.writer = writer
        self.supervisor = supervisor
        self.docker = docker
        self.github = github
        self.interval_s = interval_s

    def enabled(self) -> bool:
        return self.writer.factory_enabled()

    def _live_ids(self) -> list[str]:
        live = set(self.writer.running_task_ids())
        if self.supervisor is not None:
            live.update(self.supervisor.live)
        return sorted(live)

    async def _cancel(self, task_id: str, *, reason: str) -> None:
        if self.supervisor is not None:
            await self.supervisor.cancel(task_id, reason=reason)

    async def _kill_containers(self) -> list[str]:
        if self.docker is None:
            return []
        names = await self.docker.task_container_names()
        for name in names:
            await self.docker.kill(name)
            await self.docker.remove(name)
        return names

    async def fan_out(
        self, *, reason: str = "kill_switch"
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Cancel every live task at once, close its rows, comment; then kill the containers."""
        ids = self._live_ids()
        await asyncio.gather(*(self._cancel(task_id, reason=reason) for task_id in ids))
        cancelled: list[str] = []
        for task_id in ids:
            task = self.writer.get_task(task_id)
            if task is None or task["status"] in (
                str(TaskStatus.CANCELLED),
                str(TaskStatus.MERGED),
            ):
                continue
            last_phase = self.writer.last_completed_phase(task_id)
            self.writer.close_open_work(task_id, error="kill_switch")
            self.writer.set_task_status(task_id, TaskStatus.CANCELLED)
            cancelled.append(task_id)
            if self.github is not None:
                with contextlib.suppress(Exception):  # the comment is best effort; the trace is not
                    await self.github.create_comment(
                        int(task["issue_number"]),
                        messages.cancelled(last_phase, cause=messages.CANCEL_CAUSE_KILL_SWITCH),
                        task_id=task_id,
                    )
        containers = await self._kill_containers()
        return tuple(cancelled), tuple(containers)

    async def disable(self, *, actor: str, reason: str) -> StopReport:
        """Flag 0, the fan-out, one ``control`` event with the report."""
        started = time.monotonic()
        self.writer.set_factory_enabled(False)
        cancelled, containers = await self.fan_out()
        elapsed = round(time.monotonic() - started, 1)
        live_after = self.writer.count_open_processes()
        seq = self.writer.write_event(
            EventKind.CONTROL,
            actor,
            payload={
                "action": "kill_switch",
                "enabled": 0,
                "reason": reason,
                "cancelled": list(cancelled),
                "containers": list(containers),
                "live_processes": live_after,
                "elapsed_s": elapsed,
            },
        )
        return StopReport(False, seq, cancelled, containers, live_after, elapsed)

    async def enable(self, *, actor: str, reason: str) -> StopReport:
        """Flag 1 with a reason; re-admits nothing (PRD §Capability 5 · Re-enabling)."""
        self.writer.set_factory_enabled(True)
        seq = self.writer.write_event(
            EventKind.CONTROL,
            actor,
            payload={"action": "kill_switch", "enabled": 1, "reason": reason},
        )
        return StopReport(True, seq, (), (), self.writer.count_open_processes(), 0.0)

    async def run(self, stop: asyncio.Event) -> None:
        """Honour a flag written by another process (``factory stop``) within the bound."""
        while not stop.is_set():
            if not self.enabled() and self._live_ids():
                cancelled, containers = await self.fan_out()
                self.writer.write_event(
                    EventKind.LOG,
                    "orchestrator",
                    payload={
                        "kill_switch": "fan_out",
                        "cancelled": list(cancelled),
                        "containers": list(containers),
                    },
                )
            try:
                await asyncio.wait_for(stop.wait(), self.interval_s)
            except TimeoutError:
                continue
