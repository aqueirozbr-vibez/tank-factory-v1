"""Unit 53: schemathesis drives every documented read endpoint against the committed schema."""

import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

schemathesis = pytest.importorskip("schemathesis")

from orchestrator.api.app import ApiDeps, create_app  # noqa: E402
from orchestrator.intake.webhook import GitHubIpRanges  # noqa: E402
from orchestrator.models.settings import Settings  # noqa: E402
from orchestrator.trace import TraceDB, TraceWriter  # noqa: E402
from tests.conftest import DEFAULT_VENDORS  # noqa: E402

LOCAL = {"X-Forwarded-For": "127.0.0.1", "X-Factory-Actor": "mlima"}
SCRATCH = Path(tempfile.mkdtemp(prefix="factory-contract-"))


def _app() -> object:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    for stale in SCRATCH.glob("trace.db*"):
        stale.unlink()
    db = TraceDB.open(SCRATCH / "trace.db")
    writer = TraceWriter(db)
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
    settings = Settings(trusted_proxy_header="X-Forwarded-For", data_dir=SCRATCH)
    return create_app(
        ApiDeps(writer=writer, settings=settings, ip_ranges=GitHubIpRanges((), datetime.now(UTC)))
    )


schema = schemathesis.openapi.from_asgi("/openapi.json", _app())


@schema.parametrize()  # type: ignore[untyped-decorator]
def test_read_endpoints_follow_the_schema(case: object) -> None:
    operation = case.operation  # type: ignore[attr-defined]
    if operation.method.upper() != "GET" or not operation.path.startswith("/api/v1"):
        pytest.skip("writes and intake are covered by their own tests")
    if operation.path.endswith("/stream") or operation.path.endswith("/changes"):
        pytest.skip("streams do not end")
    case.call_and_validate(headers=LOCAL)  # type: ignore[attr-defined]
