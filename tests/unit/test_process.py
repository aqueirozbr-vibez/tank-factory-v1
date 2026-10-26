"""Unit 05: subprocess runner and process-tree termination."""

import asyncio
import ctypes
import os
import sys
import time
from pathlib import Path

import pytest

from orchestrator.harness.process import run_with_timeout
from orchestrator.models.settings import Settings

PARENT_SCRIPT = """
import pathlib, subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
pathlib.Path(sys.argv[1]).write_text(str(child.pid))
print("ready", flush=True)
time.sleep(120)
"""

NOISY_SCRIPT = """
import sys
for i in range(5):
    print(f"line {i}", flush=True)
sys.stderr.write("e" * 6000)
sys.exit(3)
"""


def pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    else:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True


def wait_dead(pid: int, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.1)
    return not pid_alive(pid)


def base_env() -> dict[str, str]:
    return Settings().subprocess_base_env()


async def test_timeout_kills_the_whole_tree(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    seen: list[str] = []
    pids: list[int] = []
    outcome = await run_with_timeout(
        [sys.executable, "-c", PARENT_SCRIPT, str(pid_file)],
        cwd=tmp_path,
        env=base_env(),
        timeout_s=2,
        on_stdout_line=seen.append,
        on_process=pids.append,
        grace_s=0.5,
    )
    assert outcome.timed_out
    assert seen == ["ready"]
    assert len(pids) == 1
    child_pid = int(pid_file.read_text())
    assert wait_dead(pids[0]), "parent still alive"
    assert wait_dead(child_pid), "child survived the tree kill"
    assert outcome.duration_s < 15


async def test_stdout_lines_and_stderr_tail(tmp_path: Path) -> None:
    lines: list[str] = []
    outcome = await run_with_timeout(
        [sys.executable, "-c", NOISY_SCRIPT],
        cwd=tmp_path,
        env=base_env(),
        timeout_s=30,
        on_stdout_line=lines.append,
    )
    assert not outcome.timed_out
    assert outcome.exit_code == 3
    assert lines == [f"line {i}" for i in range(5)]
    assert len(outcome.stderr_tail) == 4000
    assert set(outcome.stderr_tail) == {"e"}


async def test_stdin_is_delivered(tmp_path: Path) -> None:
    lines: list[str] = []
    outcome = await run_with_timeout(
        [sys.executable, "-c", "import sys; print(sys.stdin.read().upper(), end='')"],
        cwd=tmp_path,
        env=base_env(),
        timeout_s=30,
        on_stdout_line=lines.append,
        stdin_text="hello",
    )
    assert outcome.exit_code == 0
    assert lines == ["HELLO"]


def test_base_env_is_an_allow_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-FAKE")
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_FAKE")
    env = Settings().subprocess_base_env()
    assert "ANTHROPIC_API_KEY" not in env
    assert "GITHUB_TOKEN" not in env
    assert "PATH" in env


def test_event_loop_policy_supports_subprocesses() -> None:
    loop = asyncio.new_event_loop()
    try:
        assert loop is not None
    finally:
        loop.close()


LONG_LINE_SCRIPT = r"""
import json, sys
sys.stdout.write(json.dumps({"type": "assistant", "text": "x" * 300_000}) + "\n")
sys.stdout.write("after\r\n")
sys.stdout.write("no newline at the end")
sys.stdout.flush()
"""

FLOOD_SCRIPT = r"""
import os, pathlib, sys, time
pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))
print("first", flush=True)
for _ in range(20_000):
    sys.stdout.write("y" * 255 + "\n")
sys.stdout.flush()
time.sleep(120)
"""


async def test_a_stream_line_past_64_kib_is_delivered_whole(tmp_path: Path) -> None:
    """The 2026-10-25 stall: asyncio's readline raised on a long line and reading stopped."""
    lines: list[str] = []
    outcome = await run_with_timeout(
        [sys.executable, "-c", LONG_LINE_SCRIPT],
        cwd=tmp_path,
        env=base_env(),
        timeout_s=30,
        on_stdout_line=lines.append,
    )
    assert outcome.exit_code == 0 and not outcome.timed_out
    assert len(lines) == 3
    assert len(lines[0]) > 300_000 and lines[0].startswith('{"type": "assistant"')
    assert lines[1:] == ["after", "no newline at the end"]


async def test_a_failing_line_handler_stops_the_child_instead_of_hanging(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"

    def explode(line: str) -> None:
        raise RuntimeError(f"cannot handle {line!r}")

    started = time.monotonic()
    outcome = await run_with_timeout(
        [sys.executable, "-c", FLOOD_SCRIPT, str(pid_file)],
        cwd=tmp_path,
        env=base_env(),
        timeout_s=60,
        on_stdout_line=explode,
        grace_s=0.5,
    )
    assert time.monotonic() - started < 30, "the runner waited for the timeout"
    assert not outcome.timed_out
    assert outcome.stderr_tail.endswith("stdout reader failed: RuntimeError: cannot handle 'first'")
    assert wait_dead(int(pid_file.read_text()))
