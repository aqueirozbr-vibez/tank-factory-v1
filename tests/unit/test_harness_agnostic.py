"""Unit 42: the same build slice on either adapter, the two-vendor check and the version probe."""

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import pytest

from cli.serve import serve
from orchestrator.config import load_factory_config
from orchestrator.errors import ConfigError, HarnessError
from orchestrator.harness.base import HarnessAdapter
from orchestrator.harness.claude_code import ClaudeCodeHarness
from orchestrator.harness.codex import CodexHarness
from orchestrator.harness.process import ProcessOutcome
from orchestrator.harness.registry import probe_harness_versions
from orchestrator.issue_to_pr import run_build_slice
from orchestrator.models.config import FactoryConfig, QualityCommands
from orchestrator.models.enums import AgentRole, WorkspaceKind
from orchestrator.models.settings import Settings
from orchestrator.phases.build import BuildDeps
from orchestrator.trace import TraceWriter
from orchestrator.workspace.hooks import codex_rules
from orchestrator.workspace.worktree import create_worktree
from tests.unit.test_build_phase import OK, SPEC_REL, SRC, TASK_ID, TEST
from tests.unit.test_claude_code import STREAMS, ReplayRunner
from tests.unit.test_codex import VERSION as CODEX_VERSION
from tests.unit.test_container import HOME_HOST, IMAGE, FakeDocker
from tests.unit.test_review_phase import ACCEPTED
from tests.unit.test_runner import PLAN, Stack, prepare
from tests.unit.test_spec_gate import TASK

ROOT = Path(__file__).resolve().parents[2]
CLAUDE_VERSION = "2.1.309 (Claude Code)"


class EffectfulReplay(ReplayRunner):
    """A recorded stream plus the edits the agent made: the worktree ends as the stream claims."""

    async def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_s: float,
        on_stdout_line: Callable[[str], None] | None = None,
        on_process: Callable[[int], None] | None = None,
        stdin_text: str | None = None,
    ) -> ProcessOutcome:
        (cwd / "src" / "billing" / "invoice.py").write_text(SRC, encoding="utf-8", newline="\n")
        (cwd / "tests" / "unit" / "test_invoice_due_date.py").write_text(
            TEST, encoding="utf-8", newline="\n"
        )
        return await super().__call__(
            argv,
            cwd=cwd,
            env=env,
            timeout_s=timeout_s,
            on_stdout_line=on_stdout_line,
            on_process=on_process,
            stdin_text=stdin_text,
        )


def config_for(harness: str) -> FactoryConfig:
    cfg = load_factory_config(ROOT / "fixtures" / "factory_config")
    quality = QualityCommands(format=OK, lint=OK, typecheck=OK, test=OK, full_suite=OK)
    roster = cfg.roster
    if harness == "codex":  # two vendors stay: the reviewer moves to Claude Code
        builder = roster.builder.model_copy(
            update={"harness": "codex", "vendor": "openai", "model": "gpt-5-codex"}
        )
        reviewer = roster.reviewer.model_copy(
            update={"harness": "claude_code", "vendor": "anthropic", "model": "claude-opus-5-5"}
        )
        roster = roster.model_copy(update={"builder": builder, "reviewer": reviewer})
    return cfg.model_copy(update={"roster": roster, "quality": quality})


def adapter_for(harness: str) -> HarnessAdapter:
    if harness == "codex":
        return CodexHarness(
            "codex",
            runner=EffectfulReplay(STREAMS / "codex_build_green.jsonl"),
            version_probe=lambda: CODEX_VERSION,
        )
    return ClaudeCodeHarness(
        "claude",
        runner=EffectfulReplay(STREAMS / "claude_build_green.jsonl"),
        version_probe=lambda: CLAUDE_VERSION,
    )


@pytest.mark.parametrize(
    ("harness", "version", "tools"),
    [
        ("claude_code", "2.1.309", {"Read", "Edit", "Bash", "StructuredOutput"}),
        ("codex", "0.112.0", {"shell", "apply_patch"}),
    ],
)
async def test_build_slice_passes_on_each_adapter(
    harness: str,
    version: str,
    tools: set[str],
    product: Path,
    tmp_path: Path,
    trace_writer: TraceWriter,
) -> None:
    cfg = config_for(harness)
    trace_writer.load_config_version(**cfg.config_version_row())
    trace_writer.insert_task(
        task_id=TASK_ID,
        issue_number=418,
        title="Invoice due date",
        branch="agent/418",
        workspace_kind=WorkspaceKind.WORKTREE,
        budget_usd=6.0,
        admitted_by="rsouza",
        config_sha=cfg.config_sha,
    )
    deps = BuildDeps(
        writer=trace_writer,
        config=cfg,
        settings=Settings(),
        harness=adapter_for(harness),
        factory_version="0.1.0-test",
    )
    wt = create_worktree(product, issue=418, worktrees_dir=tmp_path / "w")
    outcome = await run_build_slice(deps, task_id=TASK_ID, worktree=wt, spec_rel=SPEC_REL)
    assert outcome.passed and outcome.commit_sha is not None and outcome.repairs == 0
    rows = trace_writer.db.fetchall(
        "SELECT name, status, harness, harness_version FROM phases ORDER BY seq"
    )
    assert [(r["name"], r["status"]) for r in rows] == [
        ("build", "pass"),
        ("code_gates", "pass"),
        ("commit", "pass"),
    ]
    assert (rows[0]["harness"], rows[0]["harness_version"]) == (harness, version)
    gates = trace_writer.db.fetchall("SELECT gate FROM gate_results ORDER BY created_at, rowid")
    assert [g["gate"] for g in gates] == [
        "envelope_schema",
        "permissions_enforce",
        "artifacts_exist",
        "files_non_empty",
        "diff_matches_claims",
        "format",
        "lint",
        "typecheck",
        "focused_tests",
    ]
    calls = trace_writer.db.fetchall(
        "SELECT json_extract(payload_json, '$.tool') AS tool FROM events WHERE kind = 'tool_call'"
    )
    assert {c["tool"] for c in calls} == tools
    start = trace_writer.db.fetchone("SELECT payload_json FROM events WHERE kind = 'agent_start'")
    assert start is not None and f'"harness":"{harness}"' in start["payload_json"].replace(" ", "")


def test_one_vendor_roster_is_refused(tmp_path: Path) -> None:
    factory_dir = tmp_path / ".factory"
    source = ROOT / "fixtures" / "factory_config"
    factory_dir.mkdir()
    for file in source.iterdir():
        (factory_dir / file.name).write_bytes(file.read_bytes())
    roster = (factory_dir / "roster.yaml").read_text(encoding="utf-8")
    roster = roster.replace(
        "builder:\n  harness: claude_code\n  model: claude-opus-5-5",
        "builder:\n  harness: codex\n  model: gpt-5-codex",
    )
    (factory_dir / "roster.yaml").write_text(roster, encoding="utf-8", newline="\n")
    with pytest.raises(ConfigError, match="two vendors"):
        load_factory_config(factory_dir)


def test_serve_refuses_a_bad_roster_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def broken(*_args: object, **_kwargs: object) -> object:
        raise ConfigError("roster.yaml: builder and reviewer share one vendor")

    monkeypatch.setattr("cli.serve.build_service", broken)
    code = serve(
        repo_clone=tmp_path,
        org="acme",
        repo="atlas-api",
        budget_usd=6.0,
        harness="roster",
        scripts_dir=None,
        data_dir=tmp_path / "data",
        bind=None,
        port=None,
        with_poller=False,
        poll_interval_s=None,
    )
    assert code == 2
    assert "share one vendor" in capsys.readouterr().err


def test_startup_probes_every_harness_version() -> None:
    versions = probe_harness_versions(
        {
            AgentRole.PLANNER: ClaudeCodeHarness("claude", version_probe=lambda: CLAUDE_VERSION),
            AgentRole.BUILDER: ClaudeCodeHarness("claude", version_probe=lambda: CLAUDE_VERSION),
            AgentRole.REVIEWER: CodexHarness("codex", version_probe=lambda: CODEX_VERSION),
        }
    )
    assert versions == {"planner": "2.1.309", "builder": "2.1.309", "reviewer": "0.112.0"}
    with pytest.raises(HarnessError):  # a binary whose version cannot be read refuses to run
        probe_harness_versions({AgentRole.BUILDER: ClaudeCodeHarness("no-such-binary-f8c1")})


async def test_container_start_installs_the_codex_rules(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    prepare(product, tmp_path)
    docker = FakeDocker()
    stack = Stack(
        trace_writer,
        product,
        tmp_path,
        [PLAN, STREAMLESS_GREEN, ACCEPTED],
        workspace="container",
        docker=docker,
        task_image=IMAGE,
    )
    stack.admit()
    report = await stack.runner.run(TASK)
    assert report.delivered
    installs = [
        argv
        for argv, _env, _cwd in docker.execs
        if argv and argv[0] == "python3" and argv[-1].endswith("/.codex/rules/factory.rules")
    ]
    assert len(installs) == 1 and installs[0][-1] == "/home/agent/.codex/rules/factory.rules"
    written = Path(HOME_HOST) / ".codex" / "rules" / "factory.rules"
    assert written.read_text(encoding="utf-8") == codex_rules()


from tests.unit.test_build_phase import GREEN as STREAMLESS_GREEN  # noqa: E402
