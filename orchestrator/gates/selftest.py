"""Startup self-test: a quality command that passes a known-failing fixture is a placeholder.

The factory refuses to start on any exit 0 (PRD §Capability 3, §Risks ·
Placeholder gates; architecture.md · Invariant 19).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from orchestrator.errors import ConfigError
from orchestrator.gates.base import Check
from orchestrator.harness.claude_code import Runner
from orchestrator.harness.process import run_with_timeout
from orchestrator.models.config import QualityCommands

SELF_TEST_TIMEOUT_S = 10 * 60


@dataclass(frozen=True)
class SelfTestReport:
    ok: bool
    checks: tuple[Check, ...]

    @property
    def placeholders(self) -> list[str]:
        return [c.item for c in self.checks if not c.ok]


async def self_test(
    commands: QualityCommands,
    fixture_dir: Path,
    *,
    env: Mapping[str, str],
    timeout_s: float = SELF_TEST_TIMEOUT_S,
    runner: Runner = run_with_timeout,
) -> SelfTestReport:
    """Run every configured command in the known-failing fixture; each must be found and fail."""
    if not fixture_dir.is_dir():
        raise ConfigError(f"known-failing fixture directory not found: {fixture_dir}")
    checks: list[Check] = []
    for name, argv in (
        ("format", commands.format),
        ("lint", commands.lint),
        ("typecheck", commands.typecheck),
        ("test", commands.test),
        ("full_suite", commands.full_suite),
    ):
        command = " ".join(argv)
        try:
            outcome = await runner(list(argv), cwd=fixture_dir, env=env, timeout_s=timeout_s)
        except FileNotFoundError:
            checks.append(Check(name, False, f"command not found: {command}"))
            continue
        if outcome.timed_out:
            checks.append(Check(name, False, f"timed out: {command}"))
        elif outcome.exit_code == 0:
            checks.append(Check(name, False, f"exits 0 on the known-failing fixture: {command}"))
        else:
            checks.append(Check(name, True, f"exit {outcome.exit_code}: {command}"))
    return SelfTestReport(ok=all(c.ok for c in checks), checks=tuple(checks))


def assert_self_test(report: SelfTestReport) -> None:
    if report.ok:
        return
    problems = "; ".join(f"{c.item}: {c.note}" for c in report.checks if not c.ok)
    raise ConfigError(
        "refusing to start: a quality command is a placeholder or unavailable: " + problems,
        detail={"commands": report.placeholders},
    )
