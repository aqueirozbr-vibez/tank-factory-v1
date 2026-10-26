"""Unit 06: the Claude Code adapter against recorded streams, no network."""

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import pytest

from orchestrator.errors import HarnessError
from orchestrator.harness import HarnessRequest, ToolCall
from orchestrator.harness.claude_code import ClaudeCodeHarness, StreamParser, parse_version
from orchestrator.harness.process import ProcessOutcome
from orchestrator.models.enums import AgentRole

STREAMS = Path(__file__).resolve().parents[2] / "fixtures" / "streams"
BUNDLED_BINARY = (
    Path.home()
    / ".vscode"
    / "extensions"
    / "anthropic.claude-code-2.1.309-win32-x64"
    / "resources"
    / "native-binary"
    / "claude.exe"
)


class ReplayRunner:
    """Feeds a recorded stream to the parser and returns a scripted outcome."""

    def __init__(
        self, stream: Path | None, *, exit_code: int | None = 0, timed_out: bool = False
    ) -> None:
        self.stream = stream
        self.exit_code = exit_code
        self.timed_out = timed_out
        self.argv: list[str] = []
        self.stdin_text: str | None = None

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
        self.argv = list(argv)
        self.stdin_text = stdin_text
        if on_process is not None:
            on_process(4242)
        if self.stream is not None and on_stdout_line is not None:
            for line in self.stream.read_text(encoding="utf-8").splitlines():
                on_stdout_line(line)
        return ProcessOutcome(
            exit_code=self.exit_code,
            timed_out=self.timed_out,
            stderr_tail="" if not self.timed_out else "killed",
            duration_s=1.0,
        )


def _request(tmp_path: Path, *, resume: str | None = None) -> HarnessRequest:
    return HarnessRequest(
        role=AgentRole.BUILDER,
        prompt="Implement specs/418-invoice-due-date.md.",
        output_schema={"type": "object", "required": ["status"]},
        cwd=tmp_path,
        env={"PATH": "/usr/bin"},
        model="claude-opus-5-5",
        timeout_s=7200,
        system_prompt="You are the builder.",
        tools=("Read", "Edit", "Bash"),
        max_turns=80,
        budget_usd=3.0,
        resume_session_id=resume,
    )


def test_build_argv_is_exact(tmp_path: Path) -> None:
    harness = ClaudeCodeHarness("claude", version_probe=lambda: "2.1.309 (Claude Code)")
    argv = harness.build_argv(_request(tmp_path, resume="sess-1"))
    assert argv == [
        "claude",
        "-p",
        "--bare",
        "--output-format",
        "stream-json",
        "--verbose",
        "--json-schema",
        '{"required":["status"],"type":"object"}',
        "--model",
        "claude-opus-5-5",
        "--permission-mode",
        "dontAsk",
        "--permission-prompts",
        "none",
        "--max-turns",
        "80",
        "--max-budget-usd",
        "3.00",
        "--tools",
        "Read,Edit,Bash",
        "--allowedTools",
        "Read,Edit,Bash",
        "--system-prompt",
        "You are the builder.",
        "--resume",
        "sess-1",
    ]
    assert "--bare" not in ClaudeCodeHarness("claude", bare=False).build_argv(_request(tmp_path))


async def test_green_stream_replays(tmp_path: Path) -> None:
    runner = ReplayRunner(STREAMS / "claude_build_green.jsonl")
    harness = ClaudeCodeHarness("claude", runner=runner, version_probe=lambda: "2.1.309")
    calls: list[ToolCall] = []
    pids: list[int] = []
    result = await harness.run(
        _request(tmp_path), on_tool_call=calls.append, on_process=pids.append
    )
    assert runner.stdin_text == "Implement specs/418-invoice-due-date.md."
    assert pids == [4242]
    assert result.completed
    assert result.harness_version == "2.1.309"
    assert result.session_id == "7d2a4c8e-5b1f-4e3a-9c2d-1f0e8a7b6c5d"
    assert result.usage.cost_usd == pytest.approx(0.4138)
    assert (
        result.usage.input_tokens,
        result.usage.output_tokens,
        result.usage.cache_read_tokens,
        result.usage.cache_write_tokens,
    ) == (18450, 2210, 12000, 640)
    assert result.num_turns == 5 and result.retries == 0
    assert [c.tool for c in calls] == ["Read", "Edit", "Bash", "StructuredOutput"]
    assert [c.ok for c in calls] == [True, True, True, True]
    assert calls[0].tool_call_id == "toolu_01A"
    assert calls[0].args == {"file_path": "src/billing/invoice.py"}
    assert calls[1].result_snippet == "The file src/billing/invoice.py has been updated."
    assert all(c.duration_ms is not None and c.duration_ms >= 0 for c in calls)
    assert result.envelope_text is not None
    envelope = json.loads(result.envelope_text)
    assert envelope["commit_message"] == "feat(billing): invoice due_date (#418)"
    assert envelope["changed_files"] == [
        "src/billing/invoice.py",
        "tests/unit/test_invoice_due_date.py",
    ]


async def test_max_turns_stream_is_an_error_with_usage(tmp_path: Path) -> None:
    runner = ReplayRunner(STREAMS / "claude_build_max_turns.jsonl", exit_code=1)
    harness = ClaudeCodeHarness("claude", runner=runner, version_probe=lambda: "2.1.309")
    calls: list[ToolCall] = []
    result = await harness.run(_request(tmp_path), on_tool_call=calls.append)
    assert result.outcome == "error"
    assert result.error == "error_max_turns: Reached max turns (80)"
    assert result.envelope_text is None
    assert result.usage.cost_usd == pytest.approx(2.9871)
    assert result.num_turns == 80
    assert result.retries == 1
    assert result.exit_code == 1
    assert [c.ok for c in calls] == [False]


async def test_cut_stream_with_sigterm_is_killed(tmp_path: Path) -> None:
    runner = ReplayRunner(STREAMS / "claude_build_cut.jsonl", exit_code=143)
    harness = ClaudeCodeHarness("claude", runner=runner, version_probe=lambda: "2.1.309")
    calls: list[ToolCall] = []
    result = await harness.run(_request(tmp_path), on_tool_call=calls.append)
    assert result.outcome == "killed"
    assert result.session_id == "5a4b3c2d-1e0f-4a9b-8c7d-6e5f4a3b2c1d"
    assert result.error is not None and "exit code 143" in result.error
    assert [(c.tool, c.ok) for c in calls] == [("Read", None)]


async def test_cut_stream_with_other_exit_is_an_error(tmp_path: Path) -> None:
    runner = ReplayRunner(STREAMS / "claude_build_cut.jsonl", exit_code=2)
    harness = ClaudeCodeHarness("claude", runner=runner, version_probe=lambda: "2.1.309")
    result = await harness.run(_request(tmp_path), on_tool_call=lambda _: None)
    assert result.outcome == "error"


async def test_runner_timeout_is_a_timeout(tmp_path: Path) -> None:
    runner = ReplayRunner(None, exit_code=None, timed_out=True)
    harness = ClaudeCodeHarness("claude", runner=runner, version_probe=lambda: "2.1.309")
    result = await harness.run(_request(tmp_path, resume="keep-me"), on_tool_call=lambda _: None)
    assert result.outcome == "timeout"
    assert result.session_id == "keep-me"
    assert result.error == "timed out after 7200 s"


def test_parser_counts_unparsable_lines_and_flushes_pending() -> None:
    calls: list[ToolCall] = []
    parser = StreamParser(calls.append)
    for line in (STREAMS / "claude_build_cut.jsonl").read_text(encoding="utf-8").splitlines():
        parser.feed(line)
    parser.feed("")
    parser.feed("[1, 2]")
    assert parser.unparsed_lines == 2
    assert parser.model == "claude-opus-5-5"
    assert calls == []
    parser.finish()
    assert len(calls) == 1 and calls[0].ok is None and calls[0].result_snippet is None


def test_orphan_tool_result_is_recorded_as_unknown() -> None:
    calls: list[ToolCall] = []
    parser = StreamParser(calls.append)
    parser.feed(
        json.dumps(
            {
                "type": "user",
                "message": {
                    "content": [{"type": "tool_result", "tool_use_id": "nope", "content": "x"}]
                },
            }
        )
    )
    assert [(c.tool, c.tool_call_id, c.ok) for c in calls] == [("unknown", "nope", True)]


def test_success_with_is_error_is_an_error(tmp_path: Path) -> None:
    runner = ReplayRunner(None)
    harness = ClaudeCodeHarness("claude", runner=runner, version_probe=lambda: "2.1.309")
    parser = StreamParser(lambda _: None)
    parser.feed(
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": True,
                "result": "Invalid API key",
                "session_id": "s",
                "total_cost_usd": 0,
                "usage": {},
                "num_turns": 0,
            }
        )
    )
    result = harness._to_result(_request(tmp_path), parser, ProcessOutcome(1, False, "", 0.1))
    assert result.outcome == "error" and result.error == "success: Invalid API key"


def test_version_parsing() -> None:
    assert parse_version("2.1.309 (Claude Code)") == "2.1.309"
    assert parse_version("2.1.309\n") == "2.1.309"
    with pytest.raises(HarnessError):
        parse_version("Claude Code")
    with pytest.raises(HarnessError):
        parse_version("")
    harness = ClaudeCodeHarness("claude", version_probe=lambda: "2.1.309 (Claude Code)")
    assert harness.version() == "2.1.309"


def test_missing_binary_raises() -> None:
    with pytest.raises(HarnessError, match="did not answer --version"):
        ClaudeCodeHarness("definitely-not-a-binary-xyz").version()


@pytest.mark.skipif(not BUNDLED_BINARY.is_file(), reason="bundled Claude Code binary not present")
def test_bundled_binary_answers_version_offline() -> None:
    version = ClaudeCodeHarness(str(BUNDLED_BINARY)).version()
    assert version.split(".")[0].isdigit()
