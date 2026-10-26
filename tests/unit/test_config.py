"""Unit 04: `.factory/` configuration models and loader."""

import shutil
from pathlib import Path

import pytest
import yaml

from orchestrator import constants
from orchestrator.config import load_factory_config, tree_sha
from orchestrator.errors import ConfigError
from orchestrator.models.enums import AgentRole
from orchestrator.trace import TraceWriter

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "factory_config"


@pytest.fixture
def factory_dir(tmp_path: Path) -> Path:
    target = tmp_path / ".factory"
    shutil.copytree(FIXTURE, target)
    return target


def _write_roster(factory_dir: Path, **overrides: dict[str, object]) -> None:
    roster = yaml.safe_load((factory_dir / "roster.yaml").read_text(encoding="utf-8"))
    for role, fields in overrides.items():
        roster[role].update(fields)
    (factory_dir / "roster.yaml").write_text(yaml.safe_dump(roster), encoding="utf-8")


def test_default_fixture_loads_with_stable_sha(factory_dir: Path) -> None:
    first = load_factory_config(factory_dir)
    second = load_factory_config(factory_dir)
    assert len(first.config_sha) == 64
    assert first.config_sha == second.config_sha == tree_sha(factory_dir)
    assert first.roster.planner.harness == "claude_code"
    assert first.roster.builder.max_turns == constants.BUILDER_MAX_TURNS
    assert first.roster.reviewer.harness == "codex"
    assert first.roster.vendors() == {
        "planner": "anthropic",
        "builder": "anthropic",
        "reviewer": "openai",
    }
    assert first.roster.role(AgentRole.REVIEWER) is first.roster.reviewer
    assert first.quality.ordered()[0] == ("format", ("uv", "run", "ruff", "format", "--check", "."))
    assert first.settings.concurrency_n == 3
    assert "version: 1" in first.risk_yaml
    assert "Blocker: 100" in first.review_policy_yaml
    assert first.prompt_override_dir(AgentRole.BUILDER) is None


def test_sha_changes_with_any_byte(factory_dir: Path) -> None:
    before = load_factory_config(factory_dir).config_sha
    path = factory_dir / "review-policy.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "\n# touched\n", encoding="utf-8")
    assert load_factory_config(factory_dir).config_sha != before


def test_same_vendor_roster_is_refused(factory_dir: Path) -> None:
    _write_roster(factory_dir, reviewer={"harness": "claude_code", "model": "claude-opus-5-5"})
    with pytest.raises(ConfigError, match="vendor"):
        load_factory_config(factory_dir)


def test_reviewer_may_not_write_or_use_bash(factory_dir: Path) -> None:
    _write_roster(factory_dir, reviewer={"writes": ["src/"]})
    with pytest.raises(ConfigError, match="reviewer writes nothing"):
        load_factory_config(factory_dir)
    _write_roster(factory_dir, reviewer={"writes": [], "tools": ["Read", "Bash"]})
    with pytest.raises(ConfigError, match="Read, Glob, Grep"):
        load_factory_config(factory_dir)


def test_planner_writes_only_specs(factory_dir: Path) -> None:
    _write_roster(factory_dir, planner={"writes": ["specs/", "src/"]})
    with pytest.raises(ConfigError, match="specs/"):
        load_factory_config(factory_dir)


def test_fake_harness_needs_a_vendor(factory_dir: Path) -> None:
    _write_roster(factory_dir, builder={"harness": "fake", "model": "fake-1"})
    with pytest.raises(ConfigError, match="fake harness must name its vendor"):
        load_factory_config(factory_dir)
    _write_roster(
        factory_dir, builder={"harness": "fake", "model": "fake-1", "vendor": "anthropic"}
    )
    assert load_factory_config(factory_dir).roster.builder.vendor_name == "anthropic"


def test_missing_required_file_is_named(factory_dir: Path) -> None:
    (factory_dir / "roster.yaml").unlink()
    with pytest.raises(ConfigError, match=r"roster\.yaml is missing"):
        load_factory_config(factory_dir)
    with pytest.raises(ConfigError, match="not found"):
        load_factory_config(factory_dir / "nope")


def test_invalid_yaml_and_unknown_fields_are_refused(factory_dir: Path) -> None:
    (factory_dir / "quality.yaml").write_text("format: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_factory_config(factory_dir)
    (factory_dir / "quality.yaml").write_text(
        yaml.safe_dump(
            {
                "format": ["x"],
                "lint": ["x"],
                "typecheck": ["x"],
                "test": ["x"],
                "full_suite": ["x"],
                "deploy": ["x"],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="deploy"):
        load_factory_config(factory_dir)


def test_protected_paths_always_include_prd_set_and_lockfiles(factory_dir: Path) -> None:
    (factory_dir / "factory.yaml").write_text(
        yaml.safe_dump({"protected_paths": ["infra/"], "concurrency_n": 5}), encoding="utf-8"
    )
    cfg = load_factory_config(factory_dir)
    for path in constants.PROTECTED_PATHS_DEFAULT + constants.LOCKFILES + ("infra/",):
        assert path in cfg.protected_paths
    assert cfg.settings.concurrency_n == 5
    (factory_dir / "factory.yaml").unlink()
    assert load_factory_config(factory_dir).settings.concurrency_n == 3


def test_concurrency_outside_range_is_refused(factory_dir: Path) -> None:
    (factory_dir / "factory.yaml").write_text(
        yaml.safe_dump({"concurrency_n": 11}), encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="concurrency_n"):
        load_factory_config(factory_dir)


def test_config_version_row_inserts(factory_dir: Path, trace_writer: TraceWriter) -> None:
    cfg = load_factory_config(factory_dir, main_commit="deadbeef")
    assert trace_writer.load_config_version(**cfg.config_version_row())
    row = trace_writer.db.fetchone(
        "SELECT * FROM config_versions WHERE config_sha = ?", (cfg.config_sha,)
    )
    assert row is not None
    assert row["main_commit"] == "deadbeef"
    assert '"reviewer":"openai"' in row["vendors_json"]
    assert "uv.lock" in row["protected_paths_json"]
