"""Unit 26: the Codex adapter against hand-authored JSONL streams, no network."""

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import pytest

from orchestrator.errors import HarnessError
from orchestrator.harness import HarnessRequest, ToolCall, Usage
from orchestrator.harness.codex import (
    CodexHarness,
    CodexStreamParser,
    parse_codex_version,
    strip_fences,
)
from orchestrator.harness.process import ProcessOutcome
from orchestrator.harness.registry import make_harness
from orchestrator.models.enums import AgentRole
from orchestrator.models.envelopes import ReviewOutput
from orchestrator.models.settings import Settings
from tests.unit.test_build_phase import make_config
from tests.unit.test_claude_code import STREAMS, ReplayRunner

SCHEMA = Path("/tmp/factory-codex-x/output_schema.json")
THREAD = "0199a213-81c0-7800-8aa1-bbab2a035a53"
VERSION = "codex-cli 0.112.0"


class SchemaPeek(ReplayRunner):
    """Reads the schema file while it exists, as the real CLI would."""

    def __init__(self, stream: Path | None, **kwargs: object) -> None:
        super().__init__(stream, **kwargs)  # type: ignore[arg-type]
        self.schema_text: str | None = None

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
        schema_path = Path(list(argv)[list(argv).index("--output-schema") + 1])
        self.schema_text = schema_path.read_text(encoding="utf-8")
        return await super().__call__(
            argv,
            cwd=cwd,
            env=env,
            timeout_s=timeout_s,
            on_stdout_line=on_stdout_line,
            on_process=on_process,
            stdin_text=stdin_text,
        )


def _request(
    tmp_path: Path,
    *,
    role: AgentRole = AgentRole.REVIEWER,
    resume: str | None = None,
    thinking: str | None = "high",
    system: str | None = "You are the reviewer.",
) -> HarnessRequest:
    return HarnessRequest(
        role=role,
        prompt="Review the diff against specs/418-invoice-due-date.md.",
        output_schema={"type": "object", "required": ["approved"]},
        cwd=tmp_path,
        env={"PATH": "/usr/bin"},
        model="gpt-5-codex",
        timeout_s=2700,
        system_prompt=system,
        tools=("Read", "Glob", "Grep"),
        thinking=thinking,
        budget_usd=1.0,
        resume_session_id=resume,
    )


def test_build_argv_is_exact(tmp_path: Path) -> None:
    harness = CodexHarness("codex", version_probe=lambda: VERSION)
    assert harness.build_argv(_request(tmp_path), schema_path=SCHEMA) == [
        "codex",
        "exec",
        "--json",
        "--color",
        "never",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "-C",
        str(tmp_path),
        "-m",
        "gpt-5-codex",
        "--output-schema",
        str(SCHEMA),
        "-c",
        'approval_policy="never"',
        "-c",
        "sandbox_workspace_write.network_access=false",
        "-c",
        'model_reasoning_effort="high"',
        "-",
    ]
    resumed = harness.build_argv(
        _request(tmp_path, resume=THREAD, thinking=None), schema_path=SCHEMA
    )
    assert resumed[-3:] == ["resume", THREAD, "-"]
    assert not any(a.startswith("model_reasoning_effort") for a in resumed)
    builder = harness.build_argv(_request(tmp_path, role=AgentRole.BUILDER), schema_path=SCHEMA)
    assert builder[builder.index("--sandbox") + 1] == "workspace-write"
    assert harness.version() == "0.112.0"


def test_prompt_folds_the_system_prompt_in(tmp_path: Path) -> None:
    assert CodexHarness.compose_prompt(_request(tmp_path)) == (
        "You are the reviewer.\n\n---\n\nReview the diff against specs/418-invoice-due-date.md."
    )
    assert CodexHarness.compose_prompt(_request(tmp_path, system=None)) == (
        "Review the diff against specs/418-invoice-due-date.md."
    )


async def test_green_stream_replays(tmp_path: Path) -> None:
    runner = SchemaPeek(STREAMS / "codex_review_green.jsonl")
    harness = CodexHarness("codex", runner=runner, version_probe=lambda: VERSION)
    calls: list[ToolCall] = []
    pids: list[int] = []
    result = await harness.run(
        _request(tmp_path), on_tool_call=calls.append, on_process=pids.append
    )
    assert runner.stdin_text is not None and runner.stdin_text.startswith("You are the reviewer.")
    assert pids == [4242]
    assert runner.schema_text == '{"required":["approved"],"type":"object"}'
    schema_arg = runner.argv[runner.argv.index("--output-schema") + 1]
    assert not Path(schema_arg).exists()  # the scratch directory is gone after the run
    assert result.completed and result.harness == "codex" and result.harness_version == "0.112.0"
    assert result.session_id == THREAD and result.exit_code == 0
    assert result.usage == Usage(
        input_tokens=15230, output_tokens=1480, cache_read_tokens=9000, cache_write_tokens=0
    )
    assert result.num_turns == 1 and result.retries == 1
    assert [c.tool for c in calls] == ["shell", "shell"]
    assert calls[0].tool_call_id == "item_1" and calls[0].ok is True
    assert calls[0].args == {"command": "git diff 3f1e2d4...HEAD -- src tests"}
    assert calls[0].result_snippet is not None and "def due_date" in calls[0].result_snippet
    assert (
        calls[1].result_snippet is not None
        and "test_zero_terms_is_same_day" in calls[1].result_snippet
    )
    assert all(c.duration_ms is not None and c.duration_ms >= 0 for c in calls)
    assert result.envelope_text is not None
    envelope = ReviewOutput.model_validate_json(result.envelope_text)
    assert envelope.approved and envelope.verdict == "accepted"
    assert envelope.findings[0].line == 14 and envelope.findings[0].file == "src/billing/invoice.py"


async def test_failed_turn_is_an_error_with_the_message(tmp_path: Path) -> None:
    runner = ReplayRunner(STREAMS / "codex_review_failed.jsonl", exit_code=1)
    harness = CodexHarness("codex", runner=runner, version_probe=lambda: VERSION)
    calls: list[ToolCall] = []
    result = await harness.run(_request(tmp_path), on_tool_call=calls.append)
    assert result.outcome == "error"
    assert result.error == "turn_failed: context window exceeded for model gpt-5-codex"
    assert result.envelope_text is None and result.exit_code == 1
    assert result.session_id == "0199a2aa-1111-7000-8000-000000000002"
    assert [(c.tool, c.ok) for c in calls] == [("shell", False)]
    assert calls[0].result_snippet is not None and "bad revision" in calls[0].result_snippet


async def test_cut_stream_with_sigterm_is_killed(tmp_path: Path) -> None:
    runner = ReplayRunner(STREAMS / "codex_review_cut.jsonl", exit_code=143)
    harness = CodexHarness("codex", runner=runner, version_probe=lambda: VERSION)
    calls: list[ToolCall] = []
    result = await harness.run(_request(tmp_path), on_tool_call=calls.append)
    assert result.outcome == "killed"
    assert result.session_id == "0199a2bb-2222-7000-8000-000000000003"
    assert result.error is not None and "exit code 143" in result.error
    assert [(c.tool, c.ok, c.result_snippet) for c in calls] == [("shell", None, None)]


async def test_cut_stream_with_other_exit_is_an_error(tmp_path: Path) -> None:
    runner = ReplayRunner(STREAMS / "codex_review_cut.jsonl", exit_code=2)
    harness = CodexHarness("codex", runner=runner, version_probe=lambda: VERSION)
    result = await harness.run(_request(tmp_path), on_tool_call=lambda _: None)
    assert result.outcome == "error" and result.num_turns == 1


async def test_runner_timeout_is_a_timeout(tmp_path: Path) -> None:
    runner = ReplayRunner(None, exit_code=None, timed_out=True)
    harness = CodexHarness("codex", runner=runner, version_probe=lambda: VERSION)
    result = await harness.run(_request(tmp_path, resume="keep-me"), on_tool_call=lambda _: None)
    assert result.outcome == "timeout" and result.session_id == "keep-me"
    assert result.error == "timed out after 2700 s"


async def test_exit_one_after_a_completed_turn_is_an_error(tmp_path: Path) -> None:
    runner = ReplayRunner(STREAMS / "codex_review_green.jsonl", exit_code=1)
    harness = CodexHarness("codex", runner=runner, version_probe=lambda: VERSION)
    result = await harness.run(_request(tmp_path), on_tool_call=lambda _: None)
    assert result.outcome == "error" and result.retries == 1
    assert result.error == "stream disconnected before completion; retrying"


def test_parser_names_tools_sums_usage_and_strips_fences() -> None:
    calls: list[ToolCall] = []
    parser = CodexStreamParser(calls.append)
    for raw in ("not json", "[1]", ""):
        parser.feed(raw)
    mcp = {
        "id": "m1",
        "type": "mcp_tool_call",
        "server": "repo",
        "tool": "read_file",
        "arguments": {"path": "x"},
        "result": None,
        "error": None,
        "status": "in_progress",
    }
    parser.feed(json.dumps({"type": "item.started", "item": mcp}))
    done = {
        **mcp,
        "result": {"content": [{"type": "text", "text": "hello"}]},
        "status": "completed",
    }
    parser.feed(json.dumps({"type": "item.completed", "item": done}))
    parser.feed(
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "f1",
                    "type": "file_change",
                    "changes": [{"path": "src/a.py", "kind": "update"}],
                    "status": "failed",
                },
            }
        )
    )
    parser.feed(
        json.dumps(
            {
                "type": "item.completed",
                "item": {"id": "w1", "type": "web_search", "query": "pydantic v2", "action": "s"},
            }
        )
    )
    parser.feed(
        json.dumps(
            {"type": "item.completed", "item": {"id": "t1", "type": "todo_list", "items": []}}
        )
    )
    parser.feed(
        json.dumps(
            {
                "type": "item.completed",
                "item": {"id": "e1", "type": "error", "message": "tool crashed"},
            }
        )
    )
    parser.feed(
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "a1",
                    "type": "agent_message",
                    "text": '```json\n{"approved": false}\n```',
                },
            }
        )
    )
    parser.feed(
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 10, "cached_input_tokens": 4, "output_tokens": 2},
            }
        )
    )
    parser.feed(
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 5, "output_tokens": 1, "cache_write_input_tokens": 3},
            }
        )
    )
    assert parser.unparsed_lines == 2 and parser.turns_completed == 2
    assert [(c.tool, c.tool_call_id, c.ok) for c in calls] == [
        ("mcp:repo.read_file", "m1", True),
        ("apply_patch", "f1", False),
        ("web_search", "w1", True),
    ]
    assert calls[0].result_snippet == "hello" and calls[1].result_snippet == "update src/a.py"
    assert calls[2].args == {"query": "pydantic v2"}
    assert parser.errors == ["tool crashed"] and parser.last_message == '{"approved": false}'
    assert parser.usage == Usage(
        input_tokens=15, output_tokens=3, cache_read_tokens=4, cache_write_tokens=3
    )


def test_strip_fences_and_version() -> None:
    assert strip_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_fences("```\n{}\n```") == "{}" and strip_fences(' {"a": 1} ') == '{"a": 1}'
    assert strip_fences("plain text") == "plain text"
    assert parse_codex_version("codex-cli 0.112.0") == "0.112.0"
    assert parse_codex_version("v0.50.0\n") == "0.50.0"
    with pytest.raises(HarnessError, match="Codex version"):
        parse_codex_version("codex-cli")


def test_registry_builds_the_codex_adapter() -> None:
    reviewer = make_config().roster.reviewer.model_copy(update={"harness": "codex"})
    harness = make_harness(reviewer, Settings())
    assert isinstance(harness, CodexHarness) and harness.name == "codex"
    assert harness.binary == Settings().codex_bin
