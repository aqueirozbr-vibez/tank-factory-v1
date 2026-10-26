"""Unit 54: the authz table row by row; the generated modules match; the lookup fails closed."""

import json
from pathlib import Path
from typing import cast

import httpx
import pytest

from orchestrator.authz import (
    CASES_JSON,
    GENERATED_PY,
    TABLE_PATH,
    Case,
    decide,
    load_table,
    resolve_permission,
    verify_generated,
)
from orchestrator.authz import authz as generated
from orchestrator.authz.generate import check, generate, render_python
from orchestrator.errors import ConfigError
from orchestrator.intake.ports import GitHubPort
from orchestrator.trace import TraceWriter

TABLE = load_table()


def test_table_parses_and_the_generated_modules_match() -> None:
    table = verify_generated()
    assert table.version == 1 and len(table.rules) >= 12 and len(table.cases) >= 10
    assert set(table.permissions) == {"read", "triage", "write", "maintain", "admin"}
    assert check() == []  # authz.py, authz.ts and authz.cases.json are what the table generates
    cases = json.loads(CASES_JSON.read_text(encoding="utf-8"))
    assert cases[0]["permission"] == table.cases[0].permission and len(cases) == len(table.cases)
    assert table.sha256 == generated.TABLE_SHA256 and table.rules == generated.RULES


@pytest.mark.parametrize(
    "case", TABLE.cases, ids=lambda c: f"{c.permission or 'empty'}-{c.action}-{int(c.codeowner)}"
)
def test_every_case_row(case: Case) -> None:
    assert decide(case.permission, case.action, codeowner=case.codeowner).allowed is case.allow
    assert generated.allowed(case.permission, case.action, codeowner=case.codeowner) is case.allow


def test_decisions_name_their_reason_and_fail_closed() -> None:
    unknown = decide(None, "read")
    assert unknown.allowed is False and "unreachable" in unknown.reason
    denied = decide("write", "governed_approve")
    assert denied.allowed is False and "CODEOWNER" in denied.reason
    assert decide("write", "merge").reason.startswith("unknown action")
    assert decide("none", "read").reason == "permission 'none' grants nothing"
    assert decide("admin", "kill_switch").allowed and decide("read", "read").allowed
    assert decide("maintain", "governed_approve", codeowner=True).allowed


def test_an_unparsable_or_edited_table_refuses_start(tmp_path: Path) -> None:
    bad = tmp_path / "authz.yaml"
    bad.write_text("version: 2\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="version"):
        load_table(bad)
    bad.write_text(
        "version: 1\npermissions: [read]\nactions: {read: r}\nconditions: {always: a}\n"
        "rules:\n  - {permission: read, action: write, condition: always}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="unknown action"):
        load_table(bad)
    bad.write_text("not: [valid", encoding="utf-8")
    with pytest.raises(ConfigError, match="cannot be read"):
        load_table(bad)
    edited = tmp_path / "edited.yaml"
    edited.write_text(
        TABLE_PATH.read_text(encoding="utf-8") + "\n# a comment changes the hash\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="factory authz"):
        verify_generated(edited)


def test_generator_is_deterministic_and_check_names_stale_files(tmp_path: Path) -> None:
    outs = {
        "python_out": tmp_path / "a.py",
        "ts_out": tmp_path / "lib" / "a.ts",
        "cases_out": tmp_path / "lib" / "a.json",
    }
    table = generate(TABLE_PATH, **outs)
    rendered = outs["python_out"].read_text(encoding="utf-8")
    assert rendered == render_python(table) == GENERATED_PY.read_text(encoding="utf-8")
    assert "export function allowed" in outs["ts_out"].read_text(encoding="utf-8")
    assert check(TABLE_PATH, **outs) == []
    outs["ts_out"].write_text("stale", encoding="utf-8")
    assert check(TABLE_PATH, **outs) == [str(outs["ts_out"])]


class FlakyGitHub:
    def __init__(self) -> None:
        self.calls = 0
        self.down = False

    async def collaborator_permission(self, login: str, *, task_id: str) -> str:
        self.calls += 1
        if self.down:
            raise httpx.ConnectError("GitHub unreachable")
        return "write"


async def test_resolve_permission_caches_five_minutes_and_fails_closed(
    trace_writer: TraceWriter,
) -> None:
    flaky = FlakyGitHub()
    github = cast(GitHubPort, flaky)
    assert await resolve_permission(trace_writer, github, "rsouza", task_id="t") == "write"
    assert await resolve_permission(trace_writer, github, "rsouza", task_id="t") == "write"
    assert flaky.calls == 1  # the second answer came from the five-minute cache
    flaky.down = True
    assert await resolve_permission(trace_writer, github, "rsouza", task_id="t") == "write"
    assert await resolve_permission(trace_writer, github, "newcomer", task_id="t") is None
    assert decide(None, "budget_raise").allowed is False  # unknown plus unreachable: deny
    assert await resolve_permission(trace_writer, github, "dependabot[bot]", task_id="t") == "none"
    assert await resolve_permission(trace_writer, github, None, task_id="t") == "none"
