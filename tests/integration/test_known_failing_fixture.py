"""The shipped quality commands must all fail inside the known-failing fixture."""

from pathlib import Path

from orchestrator.config import load_factory_config
from orchestrator.gates.selftest import assert_self_test, self_test
from orchestrator.models.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_CONFIG = ROOT / "fixtures" / "factory_config"
KNOWN_FAILING = ROOT / "fixtures" / "known_failing"


async def test_default_commands_fail_the_known_failing_fixture() -> None:
    config = load_factory_config(FIXTURE_CONFIG)
    report = await self_test(
        config.quality, KNOWN_FAILING, env=Settings().subprocess_base_env(), timeout_s=300
    )
    assert report.ok, [c.note for c in report.checks if not c.ok]
    assert_self_test(report)
