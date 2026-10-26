"""Shared fixtures: a migrated trace database with an installation row."""

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from orchestrator.trace import TraceDB, TraceWriter
from orchestrator.trace.writer import dump_json
from orchestrator.workspace.git import run_git

ROOT = Path(__file__).resolve().parents[1]
SPEC_FIXTURE = ROOT / "fixtures" / "specs" / "418-invoice-due-date.md"

DEFAULT_VENDORS = dump_json({"planner": "anthropic", "builder": "anthropic", "reviewer": "openai"})


@pytest.fixture(autouse=True, scope="session")
def _no_operator_env_file(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Keep the operator's ``app/.env`` out of the suite.

    ``cli.main.main`` loads ``FACTORY_ENV_FILE`` (default ``.env``) into the process
    environment, and a later ``Settings()`` would read those values; the suite points
    it at an empty file for the session instead.
    """
    empty = tmp_path_factory.mktemp("env") / "empty.env"
    empty.write_text("", encoding="utf-8")
    previous = os.environ.get("FACTORY_ENV_FILE")
    os.environ["FACTORY_ENV_FILE"] = str(empty)
    yield
    if previous is None:
        os.environ.pop("FACTORY_ENV_FILE", None)
    else:
        os.environ["FACTORY_ENV_FILE"] = previous


@pytest.fixture
def trace_db(tmp_path: Path) -> Iterator[TraceDB]:
    database = TraceDB.open(tmp_path / "trace.db")
    yield database
    database.close()


@pytest.fixture
def trace_writer(trace_db: TraceDB) -> TraceWriter:
    """A writer with the default config version and the installation row in place."""
    writer = TraceWriter(trace_db)
    writer.load_config_version(
        config_sha="cfg1",
        main_commit="abc",
        roster_json="{}",
        risk_yaml="",
        review_policy_yaml="",
        protected_paths_json="[]",
        quality_commands_json="{}",
        vendors_json=DEFAULT_VENDORS,
    )
    writer.insert_installation(
        org="acme",
        repo="atlas-api",
        factory_version="0.1.0",
        config_sha="cfg1",
        per_task_budget_usd=6.0,
        export_pubkey="pk",
    )
    return writer


@pytest.fixture
def product(tmp_path: Path) -> Path:
    """A seeded product repository with the 418 spec committed on main."""
    root = tmp_path / "product"
    root.mkdir()
    run_git(["init", "-q", "-b", "main"], cwd=root)
    run_git(["config", "user.email", "dev@example.test"], cwd=root)
    run_git(["config", "user.name", "Dev"], cwd=root)
    run_git(["config", "core.autocrlf", "false"], cwd=root)
    (root / "README.md").write_text("# demo\n", encoding="utf-8")
    (root / "src" / "billing").mkdir(parents=True)
    (root / "src" / "billing" / "invoice.py").write_text(
        "class Invoice:\n    pass\n", encoding="utf-8"
    )
    (root / "tests" / "unit").mkdir(parents=True)
    (root / "tests" / "unit" / "__init__.py").write_text("", encoding="utf-8")
    (root / "tests" / "acceptance").mkdir()
    (root / "tests" / "acceptance" / "test_holdout.py").write_text(
        "def test_h() -> None:\n    assert True\n", encoding="utf-8"
    )
    (root / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    (root / "specs").mkdir()
    shutil.copy(SPEC_FIXTURE, root / "specs" / "418-invoice-due-date.md")
    run_git(["add", "-A"], cwd=root)
    run_git(["commit", "-q", "-m", "seed"], cwd=root)
    return root
