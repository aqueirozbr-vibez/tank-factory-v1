"""Subprocesses with explicit argv and env, and process-tree termination.

Never ``shell=True``; never a bare ``proc.kill()`` that leaves grandchildren
running (code-standards.md · Python · Subprocesses). SIGTERM, a grace period,
then SIGKILL on POSIX; ``taskkill /T`` then ``taskkill /T /F`` on Windows.
"""

import asyncio
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestrator import constants

IS_WINDOWS = sys.platform == "win32"
_STDERR_KEEP_BYTES = 16_000
_STDOUT_READ_BYTES = 65_536
# One stream line past this is handed on truncated (the parser counts it as unparsed) and the
# rest of it dropped; asyncio's readline would instead raise past 64 KiB and stop reading.
_STDOUT_LINE_MAX_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class ProcessOutcome:
    exit_code: int | None
    timed_out: bool
    stderr_tail: str
    duration_s: float


async def spawn(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    stdin: bool = False,
) -> asyncio.subprocess.Process:
    """Start ``argv`` in its own session or process group so the whole tree can be killed."""
    kwargs: dict[str, object] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        env=dict(env),
        stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **kwargs,  # type: ignore[arg-type]  # creationflags / start_new_session are platform keyword arguments
    )


async def _taskkill(pid: int, *, force: bool) -> None:
    argv = ["taskkill", "/T", "/PID", str(pid)] + (["/F"] if force else [])
    killer = await asyncio.create_subprocess_exec(
        *argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    await killer.wait()


async def terminate_tree(
    proc: asyncio.subprocess.Process, *, grace_s: float = constants.PROCESS_GRACE_S
) -> None:
    """Stop ``proc`` and every descendant: graceful first, forced after ``grace_s``."""
    if proc.returncode is not None:
        return
    if sys.platform == "win32":
        await _taskkill(proc.pid, force=False)
        try:
            await asyncio.wait_for(proc.wait(), grace_s)
        except TimeoutError:
            await _taskkill(proc.pid, force=True)
            with suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), 10)
    else:
        with suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), grace_s)
        except TimeoutError:
            with suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            with suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), 10)


async def run_with_timeout(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout_s: float,
    on_stdout_line: Callable[[str], None] | None = None,
    on_process: Callable[[int], None] | None = None,
    stdin_text: str | None = None,
    grace_s: float = constants.PROCESS_GRACE_S,
) -> ProcessOutcome:
    """Run ``argv``, stream stdout lines, keep a stderr tail, kill the tree on timeout.

    Stdout is read in chunks and split here, so a long stream line (a model turn that writes a
    whole module) is delivered whole. The pipes are always drained to the end: on Python 3.12 a
    subprocess's ``wait()`` returns only once its pipes close, so a reader that stops reading
    leaves the child blocked on a full pipe and ``wait()`` pending, even after a kill. A line
    handler that raises is therefore recorded, the tree is stopped at once, reading goes on to
    the end, and the failure ends the stderr tail.
    """
    started = time.monotonic()
    proc = await spawn(argv, cwd=cwd, env=env, stdin=stdin_text is not None)
    if on_process is not None:
        on_process(proc.pid)
    stderr_chunks: list[bytes] = []
    stderr_size = 0
    reader_errors: list[str] = []
    reader_failed = asyncio.Event()

    def fail(exc: BaseException) -> None:
        if not reader_errors:
            reader_errors.append(f"stdout reader failed: {type(exc).__name__}: {exc}")
        reader_failed.set()

    def deliver(raw: bytes) -> None:
        if on_stdout_line is None or reader_errors:
            return  # after a failure the rest is drained, not handled
        try:
            on_stdout_line(raw.decode("utf-8", "replace").rstrip("\r"))
        except Exception as exc:
            fail(exc)

    async def pump_stdout() -> None:
        assert proc.stdout is not None
        line = bytearray()
        dropping = False  # inside a line that already passed the cap
        try:
            while chunk := await proc.stdout.read(_STDOUT_READ_BYTES):
                start = 0
                while (end := chunk.find(b"\n", start)) != -1:
                    if dropping:
                        dropping = False
                    else:
                        line += chunk[start:end]
                        deliver(bytes(line))
                    line.clear()
                    start = end + 1
                if not dropping:
                    line += chunk[start:]
                    if len(line) > _STDOUT_LINE_MAX_BYTES:
                        deliver(bytes(line[:_STDOUT_LINE_MAX_BYTES]))
                        line.clear()
                        dropping = True
        except Exception as exc:  # a read error: nothing more can be drained
            fail(exc)
            return
        if line and not dropping:
            deliver(bytes(line))

    async def pump_stderr() -> None:
        nonlocal stderr_size
        assert proc.stderr is not None
        while chunk := await proc.stderr.read(4096):
            stderr_chunks.append(chunk)
            stderr_size += len(chunk)
            while stderr_size > _STDERR_KEEP_BYTES and len(stderr_chunks) > 1:
                stderr_size -= len(stderr_chunks.pop(0))

    async def feed_stdin() -> None:
        if stdin_text is None or proc.stdin is None:
            return
        proc.stdin.write(stdin_text.encode("utf-8"))
        with suppress(ConnectionError, BrokenPipeError):
            await proc.stdin.drain()
        proc.stdin.close()

    pumps = asyncio.ensure_future(asyncio.gather(pump_stdout(), pump_stderr(), feed_stdin()))
    waiter = asyncio.ensure_future(proc.wait())
    failed = asyncio.ensure_future(reader_failed.wait())
    timed_out = False
    try:
        watching: set[asyncio.Future[Any]] = {waiter, failed}
        done, _ = await asyncio.wait(
            watching, timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED
        )
        if waiter not in done:
            timed_out = failed not in done  # a failed reader stops the child before its timeout
            await terminate_tree(proc, grace_s=grace_s)
    except asyncio.CancelledError:
        # a cancelled task (cancel label, kill-switch) must not leave the child running
        await terminate_tree(proc, grace_s=grace_s)
        for future in (pumps, waiter, failed):
            future.cancel()
            with suppress(BaseException):
                await future
        raise
    finally:
        failed.cancel()
    with suppress(BaseException):
        await asyncio.wait_for(waiter, 10)
    try:
        await asyncio.wait_for(pumps, 10)
    except TimeoutError:
        pumps.cancel()
        with suppress(BaseException):
            await pumps
    tail = b"".join(stderr_chunks).decode("utf-8", "replace")
    if reader_errors:  # last, so the tail's end (what the harness quotes) names it
        tail = f"{tail}\n{reader_errors[0]}" if tail else reader_errors[0]
    if len(tail) > constants.OUTPUT_TAIL_CHARS:
        tail = tail[-constants.OUTPUT_TAIL_CHARS :]
    return ProcessOutcome(
        exit_code=proc.returncode,
        timed_out=timed_out,
        stderr_tail=tail,
        duration_s=time.monotonic() - started,
    )
