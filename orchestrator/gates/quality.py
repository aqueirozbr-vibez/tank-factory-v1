"""Quality gates run by code, in order, with the output kept as evidence (PRD §Cap. 3)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from orchestrator import constants
from orchestrator.gates.base import Check, GateResult, Violation
from orchestrator.harness.claude_code import Runner
from orchestrator.harness.process import run_with_timeout
from orchestrator.models.config import QualityCommands
from orchestrator.models.enums import GateName

DEFAULT_COMMAND_TIMEOUT_S = 30 * 60

_GATE_BY_NAME = {
    "format": GateName.FORMAT,
    "lint": GateName.LINT,
    "typecheck": GateName.TYPECHECK,
    "focused_tests": GateName.FOCUSED_TESTS,
    "full_suite": GateName.FULL_SUITE,
}


@dataclass(frozen=True)
class QualityOutcome:
    results: tuple[GateResult, ...]

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def first_failure(self) -> GateResult | None:
        return next((r for r in self.results if not r.passed), None)


class _Tail:
    """Keeps the last few thousand characters of streamed output."""

    def __init__(self, keep: int = 4 * constants.OUTPUT_TAIL_CHARS) -> None:
        self._parts: list[str] = []
        self._size = 0
        self._keep = keep

    def add(self, line: str) -> None:
        self._parts.append(line + "\n")
        self._size += len(line) + 1
        while self._size > self._keep and len(self._parts) > 1:
            self._size -= len(self._parts.pop(0))

    def text(self) -> str:
        return "".join(self._parts)


async def run_command_gate(
    gate: GateName,
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout_s: float,
    runner: Runner = run_with_timeout,
) -> GateResult:
    """One quality command as one gate; the command line and exit code are the evidence."""
    command = " ".join(argv)
    stdout = _Tail()
    try:
        outcome = await runner(
            list(argv), cwd=cwd, env=env, timeout_s=timeout_s, on_stdout_line=stdout.add
        )
    except FileNotFoundError as exc:
        return GateResult(
            gate,
            False,
            (Check(command, False, "command not found"),),
            (Violation(str(gate), f"command not found: {argv[0]} ({exc})"),),
            command=command,
            exit_code=None,
        )
    combined = stdout.text() + outcome.stderr_tail
    tail = combined[-constants.OUTPUT_TAIL_CHARS :]
    if outcome.timed_out:
        return GateResult(
            gate,
            False,
            (Check(command, False, f"timed out after {timeout_s:g} s"),),
            (Violation(str(gate), f"timed out after {timeout_s:g} s"),),
            command=command,
            exit_code=outcome.exit_code,
            output_tail=tail,
        )
    ok = outcome.exit_code == 0
    return GateResult(
        gate,
        ok,
        (Check(command, ok, f"exit {outcome.exit_code}"),),
        () if ok else (Violation(str(gate), f"{command} exited {outcome.exit_code}"),),
        command=command,
        exit_code=outcome.exit_code,
        output_tail=tail,
    )


async def run_quality(
    worktree: Path,
    commands: QualityCommands,
    *,
    env: Mapping[str, str],
    focused_tests: Sequence[str] = (),
    timeout_s: float = DEFAULT_COMMAND_TIMEOUT_S,
    runner: Runner = run_with_timeout,
) -> QualityOutcome:
    """format → lint → typecheck → focused tests; stop at the first failure."""
    results: list[GateResult] = []
    for name, argv in commands.ordered():
        full = list(argv) + (list(focused_tests) if name == "focused_tests" else [])
        result = await run_command_gate(
            _GATE_BY_NAME[name], full, cwd=worktree, env=env, timeout_s=timeout_s, runner=runner
        )
        results.append(result)
        if not result.passed:
            break
    return QualityOutcome(tuple(results))


async def run_full_suite(
    worktree: Path,
    commands: QualityCommands,
    *,
    env: Mapping[str, str],
    timeout_s: float = DEFAULT_COMMAND_TIMEOUT_S,
    runner: Runner = run_with_timeout,
) -> GateResult:
    return await run_command_gate(
        GateName.FULL_SUITE,
        list(commands.full_suite),
        cwd=worktree,
        env=env,
        timeout_s=timeout_s,
        runner=runner,
    )
