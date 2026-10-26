"""Unit 46: the PRD phase 3 gate, recorded here (offline and on Docker Desktop).

The four conditions: the builder fails 3 of 3 attempts to push, call ``gh``
or reach the internet (opt-in: a real task container on the internal
network with the hook bundle); five restarts mid-task open no duplicate PR
and no duplicate comment (unit 45's scenario); the same workflow completes
five issues on each harness (the real adapters over CLI-shaped streams
synthesised from the seed scripts, through the composition root); the
kill-switch stops a live task within 60 s (through the composition root).
Each test prints one ``GATE-EVIDENCE`` line; the tracker records them.
"""

import asyncio
import json
import os
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import pytest

from cli.seed import TASKS, planner_script_for, reviewer_script_for, script_for, seed_repository
from orchestrator import constants
from orchestrator.config import load_factory_config
from orchestrator.harness.base import HarnessAdapter
from orchestrator.harness.claude_code import ClaudeCodeHarness
from orchestrator.harness.codex import CodexHarness
from orchestrator.harness.fake import FakeScript, KeyedFakeHarness, apply_effects, issue_from_prompt
from orchestrator.harness.process import ProcessOutcome
from orchestrator.models.config import FactoryConfig
from orchestrator.models.enums import AgentRole
from orchestrator.models.settings import Settings
from orchestrator.service import FactoryService
from orchestrator.trace import TraceDB, TraceWriter
from orchestrator.workspace.container import DockerCli, create_container, ensure_network
from orchestrator.workspace.git import run_git
from orchestrator.workspace.holdout import HoldoutCheckout
from orchestrator.workspace.worktree import create_worktree
from tests.integration.test_phase2_gate import GateGitHub, labeled
from tests.unit.test_restart import test_five_restarts_open_one_pr_and_no_duplicate_comments

ROOT = Path(__file__).resolve().parents[2]
CLAUDE_VERSION = "2.1.309 (Claude Code)"
CODEX_VERSION = "codex-cli 0.112.0"
GATE_ISSUES = TASKS[:5]
DOCKER = os.environ.get("FACTORY_DOCKER_TESTS") == "1"


def evidence(name: str, **facts: object) -> None:
    print(f"GATE-EVIDENCE {name}: {json.dumps(facts, sort_keys=True)}")


# --- CLI-shaped streams from the seed scripts ---------------------------------------------------


def claude_stream(script: FakeScript, *, model: str) -> list[str]:
    session = script.session_id or "sess"
    lines = [
        json.dumps(
            {
                "type": "system",
                "subtype": "init",
                "session_id": session,
                "model": model,
                "claude_code_version": "2.1.309",
                "tools": [c.tool for c in script.tool_calls],
            }
        )
    ]
    for number, call in enumerate(script.tool_calls, start=1):
        use_id = f"toolu_{session}_{number}"
        lines.append(
            json.dumps(
                {
                    "type": "assistant",
                    "session_id": session,
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": use_id,
                                "name": call.tool,
                                "input": call.args,
                            }
                        ],
                    },
                }
            )
        )
        lines.append(
            json.dumps(
                {
                    "type": "user",
                    "session_id": session,
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": use_id,
                                "content": call.result_snippet or "ok",
                                "is_error": not (call.ok if call.ok is not None else True),
                            }
                        ],
                    },
                }
            )
        )
    usage = script.usage
    lines.append(
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "session_id": session,
                "num_turns": len(script.tool_calls) + 1,
                "result": "done",
                "total_cost_usd": usage.cost_usd,
                "usage": {
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cache_read_input_tokens": usage.cache_read_tokens,
                    "cache_creation_input_tokens": usage.cache_write_tokens,
                },
                "structured_output": script.envelope,
            }
        )
    )
    return lines


def codex_item(call_id: str, tool: str, args: object) -> dict[str, object]:
    payload = args if isinstance(args, dict) else {}
    if tool == "Bash":
        return {
            "id": call_id,
            "type": "command_execution",
            "command": str(payload.get("command", "true")),
            "aggregated_output": "ok\n",
            "exit_code": 0,
            "status": "completed",
        }
    if tool in ("Write", "Edit", "MultiEdit"):
        return {
            "id": call_id,
            "type": "file_change",
            "changes": [{"path": str(payload.get("file_path", "")), "kind": "add"}],
            "status": "completed",
        }
    command = (
        f"rg -n {payload.get('pattern', '')} {payload.get('path', '.')}"
        if tool == "Grep"
        else f"cat {payload.get('file_path', '')}"
    )
    return {
        "id": call_id,
        "type": "command_execution",
        "command": command,
        "aggregated_output": "ok\n",
        "exit_code": 0,
        "status": "completed",
    }


def codex_stream(script: FakeScript) -> list[str]:
    session = script.session_id or "thread"
    lines = [
        json.dumps({"type": "thread.started", "thread_id": session}),
        json.dumps({"type": "turn.started"}),
    ]
    for number, call in enumerate(script.tool_calls, start=1):
        item = codex_item(f"item_{number}", call.tool, call.args)
        lines.append(
            json.dumps({"type": "item.started", "item": {**item, "status": "in_progress"}})
        )
        lines.append(json.dumps({"type": "item.completed", "item": item}))
    lines.append(
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "item_final",
                    "type": "agent_message",
                    "text": json.dumps(script.envelope, sort_keys=True),
                },
            }
        )
    )
    usage = script.usage
    lines.append(
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": usage.input_tokens,
                    "cached_input_tokens": usage.cache_read_tokens,
                    "output_tokens": usage.output_tokens,
                },
            }
        )
    )
    return lines


class ScriptedRunner:
    """The CLI as the adapters drive it: the seed script for the role and issue, as a stream."""

    def __init__(self, role: AgentRole, scripts_dir: Path, flavour: str, model: str) -> None:
        self.role = role
        self.scripts_dir = scripts_dir
        self.flavour = flavour
        self.model = model
        self.played: list[tuple[str, int]] = []

    def script_for(self, issue: int) -> FakeScript:
        folder = {"planner": "planner", "reviewer": "reviewer"}.get(str(self.role), "")
        return FakeScript.from_file(self.scripts_dir / folder / f"{issue}.json")

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
        issue = issue_from_prompt(stdin_text or "")
        assert issue is not None, "the prompt names the spec"
        script = self.script_for(issue)
        self.played.append((str(self.role), issue))
        if on_process is not None:
            on_process(4242)
        apply_effects(cwd, script.effects)
        lines = (
            claude_stream(script, model=self.model)
            if self.flavour == "claude"
            else codex_stream(script)
        )
        for line in lines:
            if on_stdout_line is not None:
                on_stdout_line(line)
        return ProcessOutcome(exit_code=0, timed_out=False, stderr_tail="", duration_s=0.5)


def adapters_for(config: FactoryConfig, scripts_dir: Path) -> dict[AgentRole, HarnessAdapter]:
    harnesses: dict[AgentRole, HarnessAdapter] = {}
    for role in AgentRole:
        cfg = config.roster.role(role)
        if cfg.harness == "codex":
            runner = ScriptedRunner(role, scripts_dir, "codex", cfg.model)
            harnesses[role] = CodexHarness(
                "codex", runner=runner, version_probe=lambda: CODEX_VERSION
            )
        else:
            runner = ScriptedRunner(role, scripts_dir, "claude", cfg.model)
            harnesses[role] = ClaudeCodeHarness(
                "claude", runner=runner, version_probe=lambda: CLAUDE_VERSION
            )
    return harnesses


def config_with_builder(scratch: Path, builder: str) -> FactoryConfig:
    config = load_factory_config(scratch / ".factory")
    if builder == "claude_code":
        return config
    roster = config.roster
    codex_builder = roster.builder.model_copy(
        update={"harness": "codex", "vendor": "openai", "model": "gpt-5-codex"}
    )
    claude_reviewer = roster.reviewer.model_copy(
        update={"harness": "claude_code", "vendor": "anthropic", "model": "claude-opus-5-5"}
    )
    return config.model_copy(
        update={
            "roster": roster.model_copy(
                update={"builder": codex_builder, "reviewer": claude_reviewer}
            )
        }
    )


def scratch_repo(root: Path) -> Path:
    target = root / "demo"
    seed_repository(target)
    bare = root / "remote.git"
    run_git(["init", "--bare", "-q", str(bare)], cwd=root)
    run_git(["remote", "add", "origin", str(bare)], cwd=target)
    return target


async def gate_service(
    scratch: Path,
    data_dir: Path,
    *,
    config: FactoryConfig,
    harnesses: Mapping[AgentRole, HarnessAdapter],
) -> tuple[FactoryService, GateGitHub, TraceDB]:
    settings = Settings(data_dir=data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    db = TraceDB.open(settings.trace_db_path)
    writer = TraceWriter(db)
    writer.load_config_version(**config.config_version_row())
    writer.insert_installation(
        org="acme",
        repo="demo",
        factory_version="gate3",
        config_sha=config.config_sha,
        per_task_budget_usd=6.0,
        export_pubkey="pk",
        concurrency_n=1,
    )
    github = GateGitHub()
    holdout = HoldoutCheckout(str(scratch), settings.holdout_dir)
    holdout.ensure()
    service = FactoryService(
        settings=settings,
        writer=writer,
        config=config,
        github=github,
        repo_clone=scratch,
        harnesses=dict(harnesses),
        base_branch="main",
        holdout=holdout,
        token_for=None,
    )
    return service, github, db


# --- condition 3: the same workflow completes five issues on each harness -----------------------


@pytest.mark.parametrize("builder", ["claude_code", "codex"])
async def test_five_issues_complete_on_each_harness(builder: str, tmp_path: Path) -> None:
    scratch = scratch_repo(tmp_path / builder)
    config = config_with_builder(scratch, builder)
    harnesses = adapters_for(config, scratch / ".factory-dev" / "scripts")
    service, github, db = await gate_service(
        scratch, tmp_path / builder / "data", config=config, harnesses=harnesses
    )
    try:
        assert service.start() == []
        for task in GATE_ISSUES:
            service.inbox.put_nowait(labeled(task.issue))
        await asyncio.wait_for(service.drain(), timeout=900)
        rows = {int(r["issue_number"]): r for r in db.fetchall("SELECT * FROM tasks")}
        assert len(rows) == len(GATE_ISSUES)
        for task in GATE_ISSUES:
            row = rows[task.issue]
            assert row["status"] == "pr_open", (task.issue, row["status"], row["blocked_reason"])
            assert row["pr_number"] is not None
        assert len(github.pulls) == len(GATE_ISSUES)
        agent_rows = db.fetchall(
            "SELECT name, harness, harness_version FROM phases WHERE kind = 'agent' ORDER BY seq"
        )
        by_phase = {(r["name"], r["harness"], r["harness_version"]) for r in agent_rows}
        expected_builder = ("build", builder, "0.112.0" if builder == "codex" else "2.1.309")
        assert expected_builder in by_phase
        reviewer = "claude_code" if builder == "codex" else "codex"
        assert (
            "review",
            reviewer,
            "2.1.309" if reviewer == "claude_code" else "0.112.0",
        ) in by_phase
        assert ("plan", "claude_code", "2.1.309") in by_phase
        tool_calls = int(db.scalar("SELECT COUNT(*) FROM events WHERE kind = 'tool_call'") or 0)
        assert tool_calls >= 4 * len(GATE_ISSUES)
        evidence(
            "five_issues_per_harness",
            builder=builder,
            issues=[t.issue for t in GATE_ISSUES],
            pr_open=len(github.pulls),
            agent_phase_rows=len(agent_rows),
            tool_call_rows=tool_calls,
        )
    finally:
        db.close()


# --- condition 2: five restarts, zero duplicates -------------------------------------------------


async def test_five_restarts_open_zero_duplicate_prs(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    await test_five_restarts_open_one_pr_and_no_duplicate_comments(product, tmp_path, trace_writer)
    pulls = int(
        trace_writer.db.scalar(
            "SELECT COUNT(DISTINCT pr_number) FROM tasks WHERE pr_number IS NOT NULL"
        )
        or 0
    )
    evidence("five_restarts", restarts=5, duplicate_prs=0, duplicate_comments=0, prs=pulls)


# --- condition 4: the kill-switch stops a live task within 60 s ----------------------------------


async def test_kill_switch_stops_a_live_task_within_the_bound(tmp_path: Path) -> None:
    scratch = scratch_repo(tmp_path / "kill")
    config = load_factory_config(scratch / ".factory")
    first = GATE_ISSUES[0]
    sleeping = FakeScript.model_validate(script_for(first, violation=False)).model_copy(
        update={"sleep_s": 120.0}
    )
    keyed = KeyedFakeHarness(
        {
            ("planner", first.issue): [FakeScript.model_validate(planner_script_for(first))],
            ("builder", first.issue): [sleeping],
            ("reviewer", first.issue): [FakeScript.model_validate(reviewer_script_for(first))],
        }
    )
    service, _github, db = await gate_service(
        scratch,
        tmp_path / "kill" / "data",
        config=config,
        harnesses=dict.fromkeys(AgentRole, keyed),
    )
    stop = asyncio.Event()
    loop = asyncio.create_task(service.run(stop))
    try:
        assert service.start() == []
        service.inbox.put_nowait(labeled(first.issue))
        for _ in range(1200):  # the planner runs, then the builder's sleep starts
            await asyncio.sleep(0.1)
            live = db.scalar(
                "SELECT COUNT(*) FROM processes WHERE ended_at IS NULL AND pid IS NOT NULL"
            )
            if int(live or 0) > 0:
                break
        else:
            raise AssertionError("no live builder process appeared")
        started = time.monotonic()
        report = await service.kill_switch.disable(actor="gate", reason="phase 3 gate timing")
        elapsed = time.monotonic() - started
        assert elapsed < constants.KILL_SWITCH_BOUND_S, elapsed
        assert report.cancelled == ("acme/demo#1",) and report.live_after == 0
        task = service.writer.get_task("acme/demo#1")
        assert task is not None and task["status"] == "cancelled"
        evidence(
            "kill_switch",
            stop_seconds=round(elapsed, 1),
            bound_seconds=constants.KILL_SWITCH_BOUND_S,
        )
    finally:
        stop.set()
        await loop
        await service.drain()
        db.close()


# --- condition 1: push, gh and the internet fail 3 of 3 (opt-in: Docker) -----------------------


@pytest.mark.skipif(not DOCKER, reason="needs Docker; set FACTORY_DOCKER_TESTS=1 to run")
async def test_builder_fails_three_of_three_push_gh_and_internet_attempts(
    product: Path, tmp_path: Path
) -> None:
    docker = DockerCli(env_dir=tmp_path / "env")
    network = await ensure_network(docker, "factory-tasks")
    wt = create_worktree(product, issue=418, worktrees_dir=tmp_path / "w")
    name = "factory-task-gate3-418"
    container = await create_container(
        docker,
        image="python:3.12-slim",
        name=name,
        worktree=wt,
        network=network,
        task_id="gate/task#418",
        hooks_dir=ROOT / "deploy" / "hooks",
    )
    denied: dict[str, int] = {"git push": 0, "gh": 0, "curl": 0}
    unreachable = 0
    gh_missing = 0
    try:
        runner = docker.exec_runner(container)
        hook = [
            "python3",
            f"{constants.HOOKS_MOUNT}/{constants.HOOK_SCRIPT}",
        ]
        attempts = {
            "git push": "git push origin HEAD",
            "gh": "gh pr create --fill",
            "curl": "curl https://api.github.com",
        }
        for _ in range(3):
            for key, command in attempts.items():
                payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
                outcome = await runner(hook, cwd=wt.path, env={}, timeout_s=60, stdin_text=payload)
                if outcome.exit_code == 2 and constants.HOOK_DENIAL_PREFIX in outcome.stderr_tail:
                    denied[key] += 1
            probe = await runner(
                [
                    "python3",
                    "-c",
                    "import urllib.request;"
                    " urllib.request.urlopen('https://api.github.com', timeout=8)",
                ],
                cwd=wt.path,
                env={},
                timeout_s=60,
            )
            if probe.exit_code not in (0, None):
                unreachable += 1
            missing = await runner(["gh", "--version"], cwd=wt.path, env={}, timeout_s=60)
            if missing.exit_code not in (0, None):
                gh_missing += 1
    finally:
        await docker.remove(name)
    assert denied == {"git push": 3, "gh": 3, "curl": 3}
    assert unreachable == 3 and gh_missing == 3
    evidence(
        "push_gh_internet",
        hook_denials=denied,
        internet_unreachable=unreachable,
        gh_unavailable=gh_missing,
        network=network,
    )
