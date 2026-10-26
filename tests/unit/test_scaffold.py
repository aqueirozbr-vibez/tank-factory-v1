"""Unit 01: the scaffold exists, is typed, and carries the PRD's numbers."""

import re
from pathlib import Path

import pytest

from cli.main import main
from orchestrator import constants
from orchestrator.errors import ConfigError, FactoryError
from orchestrator.models import Settings
from orchestrator.version import factory_version

SEMVER = re.compile(r"^\d+\.\d+\.\d+")


def test_version_is_semver_like() -> None:
    assert SEMVER.match(factory_version())


def test_cli_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"factory {factory_version()}"


def test_cli_without_arguments_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert "factory" in capsys.readouterr().out


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FACTORY_ENABLED", raising=False)
    monkeypatch.delenv("FACTORY_DATA_DIR", raising=False)
    settings = Settings()
    assert settings.enabled is True
    assert settings.data_dir == Path("/var/lib/factory")
    assert settings.trace_db_path == Path("/var/lib/factory") / "trace.db"
    assert settings.console_db_path.name == "console.db"
    assert settings.timezone == "America/Sao_Paulo"


def test_settings_kill_switch_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FACTORY_ENABLED", "0")
    assert Settings().enabled is False


def test_settings_data_dir_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FACTORY_DATA_DIR", str(tmp_path))
    assert Settings().trace_db_path == tmp_path / "trace.db"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("REPAIR_CAP", 3),
        ("REVISION_CAP", 2),
        ("SPEC_CORRECTION_CAP", 2),
        ("BUILDER_MAX_TURNS", 80),
        ("PLAN_TIMEOUT_S", 1800),
        ("BUILD_TIMEOUT_S", 7200),
        ("REVIEW_TIMEOUT_S", 2700),
        ("STUCK_AFTER_S", 900),
        ("KILL_SWITCH_BOUND_S", 60),
        ("OUTPUT_TAIL_CHARS", 4000),
        ("RESULT_SNIPPET_CHARS", 1000),
        ("CONCURRENCY_DEFAULT", 3),
        ("RETENTION_DAYS_DEFAULT", 90),
        ("RISK_HIGH_THRESHOLD", 50.0),
        ("CONFORMANCE_ACCEPT", 70.0),
        ("EVAL_BLOCK_DROP_POINTS", 10.0),
        ("BUDGET_RAISE_MAX_FACTOR", 2.0),
        ("DAILY_CAP_MULTIPLIER", 3),
    ],
)
def test_constants_match_prd(name: str, value: float) -> None:
    assert getattr(constants, name) == value


def test_protected_paths_are_the_prd_set() -> None:
    assert constants.PROTECTED_PATHS_DEFAULT == (
        ".factory/",
        ".claude/",
        ".github/workflows/",
        "tests/acceptance/",
        "CODEOWNERS",
    )


def test_errors_carry_codes() -> None:
    err = ConfigError("roster names one vendor for builder and reviewer", detail={"vendor": "x"})
    assert isinstance(err, FactoryError)
    assert err.code == "config_invalid"
    assert err.detail == {"vendor": "x"}
    assert str(err) == "roster names one vendor for builder and reviewer"
