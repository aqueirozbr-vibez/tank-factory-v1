"""Unit 10: quality runner and startup self-test with scripted commands."""

import sys
from pathlib import Path

import pytest

from orchestrator.errors import ConfigError
from orchestrator.gates.quality import run_full_suite, run_quality
from orchestrator.gates.selftest import assert_self_test, self_test
from orchestrator.models.config import QualityCommands
from orchestrator.models.enums import GateName
from orchestrator.models.settings import Settings

PY = (sys.executable, "-I", "-S", "-c")
OK = (*PY, "print('fine')")
FAIL = (*PY, "import sys; print('boom'); sys.stderr.write('bad\\n'); sys.exit(1)")
SLEEP = (*PY, "import time; time.sleep(30)")
ECHO_ARGS = (*PY, "import sys; print(' '.join(sys.argv[1:]))")


def commands(**overrides: tuple[str, ...]) -> QualityCommands:
    fields: dict[str, tuple[str, ...]] = {
        "format": OK,
        "lint": OK,
        "typecheck": OK,
        "test": OK,
        "full_suite": OK,
    }
    fields.update(overrides)
    return QualityCommands(**fields)


def env() -> dict[str, str]:
    return Settings().subprocess_base_env()


async def test_all_green_runs_in_order(tmp_path: Path) -> None:
    outcome = await run_quality(tmp_path, commands(), env=env(), timeout_s=30)
    assert outcome.passed and outcome.first_failure is None
    assert [r.gate for r in outcome.results] == [
        GateName.FORMAT,
        GateName.LINT,
        GateName.TYPECHECK,
        GateName.FOCUSED_TESTS,
    ]
    for result in outcome.results:
        assert result.exit_code == 0
        assert result.command is not None and result.command.startswith(sys.executable)
        assert result.output_tail is not None and "fine" in result.output_tail


async def test_first_failure_stops_the_ladder(tmp_path: Path) -> None:
    outcome = await run_quality(tmp_path, commands(lint=FAIL), env=env(), timeout_s=30)
    assert not outcome.passed
    assert [(r.gate, r.passed) for r in outcome.results] == [
        (GateName.FORMAT, True),
        (GateName.LINT, False),
    ]
    lint = outcome.first_failure
    assert lint is not None and lint.gate is GateName.LINT
    assert lint.exit_code == 1
    assert lint.output_tail is not None
    assert "boom" in lint.output_tail and "bad" in lint.output_tail
    assert lint.violations[0].problem.endswith("exited 1")
    assert "Command:" in lint.correction_text()


async def test_focused_test_ids_are_appended(tmp_path: Path) -> None:
    outcome = await run_quality(
        tmp_path,
        commands(test=ECHO_ARGS),
        env=env(),
        focused_tests=["tests/unit/test_a.py::test_x", "tests/unit/test_b.py"],
        timeout_s=30,
    )
    focused = outcome.results[-1]
    assert focused.gate is GateName.FOCUSED_TESTS
    assert focused.output_tail is not None
    assert focused.output_tail.strip() == "tests/unit/test_a.py::test_x tests/unit/test_b.py"
    assert focused.command is not None and focused.command.endswith("tests/unit/test_b.py")


async def test_timeout_fails_the_gate(tmp_path: Path) -> None:
    outcome = await run_quality(tmp_path, commands(format=SLEEP), env=env(), timeout_s=1)
    result = outcome.results[0]
    assert not result.passed
    assert result.checks[0].note.startswith("timed out")
    assert len(outcome.results) == 1


async def test_missing_binary_fails_and_names_the_command(tmp_path: Path) -> None:
    outcome = await run_quality(
        tmp_path, commands(typecheck=("no-such-binary-xyz", "--check")), env=env(), timeout_s=30
    )
    assert [(r.gate, r.passed) for r in outcome.results] == [
        (GateName.FORMAT, True),
        (GateName.LINT, True),
        (GateName.TYPECHECK, False),
    ]
    typecheck = outcome.results[-1]
    assert "command not found" in typecheck.violations[0].problem
    assert typecheck.command == "no-such-binary-xyz --check"


async def test_full_suite_gate(tmp_path: Path) -> None:
    result = await run_full_suite(tmp_path, commands(full_suite=FAIL), env=env(), timeout_s=30)
    assert result.gate is GateName.FULL_SUITE and not result.passed and result.exit_code == 1


async def test_self_test_rejects_a_placeholder_twenty_times(tmp_path: Path) -> None:
    failing = commands(format=FAIL, lint=FAIL, typecheck=OK, test=FAIL, full_suite=FAIL)
    for _ in range(20):
        report = await self_test(failing, tmp_path, env=env(), timeout_s=30)
        assert not report.ok
        assert report.placeholders == ["typecheck"]
        with pytest.raises(ConfigError, match="typecheck"):
            assert_self_test(report)


async def test_self_test_passes_when_every_command_fails(tmp_path: Path) -> None:
    report = await self_test(
        commands(format=FAIL, lint=FAIL, typecheck=FAIL, test=FAIL, full_suite=FAIL),
        tmp_path,
        env=env(),
        timeout_s=30,
    )
    assert report.ok and report.placeholders == []
    assert [c.item for c in report.checks] == ["format", "lint", "typecheck", "test", "full_suite"]
    assert_self_test(report)


async def test_self_test_fails_on_missing_binary_and_missing_fixture(tmp_path: Path) -> None:
    report = await self_test(
        commands(format=FAIL, lint=("nope-binary",), typecheck=FAIL, test=FAIL, full_suite=FAIL),
        tmp_path,
        env=env(),
        timeout_s=30,
    )
    assert not report.ok and report.placeholders == ["lint"]
    with pytest.raises(ConfigError, match="not found"):
        await self_test(commands(), tmp_path / "missing", env=env())
