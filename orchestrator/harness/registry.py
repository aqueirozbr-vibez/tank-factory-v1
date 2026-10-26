"""Pick the adapter the roster names for a role."""

from collections.abc import Mapping, Sequence
from pathlib import Path

from orchestrator.errors import ConfigError, HarnessError
from orchestrator.harness.base import HarnessAdapter
from orchestrator.harness.claude_code import ClaudeCodeHarness, Runner, parse_version
from orchestrator.harness.codex import CodexHarness, parse_codex_version
from orchestrator.harness.fake import FakeHarness, FakeScript
from orchestrator.harness.process import run_with_timeout
from orchestrator.models.config import RoleConfig
from orchestrator.models.enums import AgentRole
from orchestrator.models.settings import Settings


def make_harness(
    role: RoleConfig,
    settings: Settings,
    *,
    fake_scripts: Sequence[FakeScript] | None = None,
    runner: Runner | None = None,
    version: str | None = None,
) -> HarnessAdapter:
    """The adapter for a role; ``version`` is what a task container reported (unit 45)."""
    active = runner or run_with_timeout

    def probe() -> str:
        assert version is not None
        return version

    version_probe = probe if version is not None else None
    if role.harness == "fake":
        if not fake_scripts:
            raise ConfigError("the fake harness needs recorded scripts")
        return FakeHarness(fake_scripts, runner=active)
    if role.harness == "claude_code":
        return ClaudeCodeHarness(
            settings.claude_bin,
            bare=settings.claude_bare,
            runner=active,
            version_probe=version_probe,
        )
    if role.harness == "codex":
        return CodexHarness(settings.codex_bin, runner=active, version_probe=version_probe)
    raise ConfigError(f"harness {role.harness!r} is not known")


def probe_harness_versions(harnesses: Mapping[AgentRole, HarnessAdapter]) -> dict[str, str]:
    """Read every roster harness's version once at start; an unreadable one raises (TRD §11)."""
    return {str(role): harness.version() for role, harness in harnesses.items()}


async def read_harness_versions(
    runner: Runner, *, binaries: Mapping[str, str], cwd: Path
) -> dict[str, str]:
    """``<binary> --version`` through ``runner``; an unreadable binary refuses to run (TRD §11)."""
    parsers = {"claude_code": parse_version, "codex": parse_codex_version}
    versions: dict[str, str] = {}
    for name, binary in binaries.items():
        lines: list[str] = []
        try:
            outcome = await runner(
                [binary, "--version"], cwd=cwd, env={}, timeout_s=60, on_stdout_line=lines.append
            )
        except OSError as exc:
            raise HarnessError(
                f"{binary} --version could not run in the task container: {exc}"
            ) from exc
        text = "\n".join(lines) or outcome.stderr_tail
        if outcome.exit_code != 0 or not text.strip():
            raise HarnessError(
                f"{binary} --version failed in the task container (exit {outcome.exit_code})"
            )
        versions[name] = parsers[name](text)
    return versions
