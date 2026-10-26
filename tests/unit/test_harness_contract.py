"""Unit 26: one harness contract, three adapters (fake, claude_code, codex)."""

import json
from pathlib import Path

import pytest

from orchestrator.harness import (
    ClaudeCodeHarness,
    FakeHarness,
    FakeScript,
    HarnessAdapter,
    HarnessRequest,
    ToolCall,
)
from orchestrator.harness.codex import CodexHarness
from orchestrator.harness.fake import FakeToolCall, FakeUsage
from orchestrator.models.enums import AgentRole
from tests.unit.test_claude_code import STREAMS, ReplayRunner

ADAPTERS = ["fake", "claude_code", "codex"]


def request(tmp_path: Path, *, timeout_s: int = 600, resume: str | None = None) -> HarnessRequest:
    return HarnessRequest(
        role=AgentRole.BUILDER,
        prompt="Do the task.",
        output_schema={"type": "object"},
        cwd=tmp_path,
        env={"PATH": "/usr/bin"},
        model="m",
        timeout_s=timeout_s,
        system_prompt="Guide.",
        thinking="high",
        resume_session_id=resume,
    )


def build(name: str, *, healthy: bool) -> HarnessAdapter:
    if name == "fake":
        script = FakeScript(
            session_id="fake-1",
            tool_calls=(FakeToolCall(tool="Read", args={"p": 1}, result_snippet="x"),),
            envelope={"status": "success"},
            usage=FakeUsage(input_tokens=1, output_tokens=1),
            sleep_s=0.0 if healthy else 30.0,
        )
        return FakeHarness([script])
    if name == "claude_code":
        runner = (
            ReplayRunner(STREAMS / "claude_build_green.jsonl")
            if healthy
            else ReplayRunner(None, exit_code=None, timed_out=True)
        )
        return ClaudeCodeHarness("claude", runner=runner, version_probe=lambda: "2.1.309")
    runner = (
        ReplayRunner(STREAMS / "codex_review_green.jsonl")
        if healthy
        else ReplayRunner(None, exit_code=None, timed_out=True)
    )
    return CodexHarness("codex", runner=runner, version_probe=lambda: "codex-cli 0.112.0")


@pytest.mark.parametrize("name", ADAPTERS)
async def test_completed_run_meets_the_contract(name: str, tmp_path: Path) -> None:
    adapter = build(name, healthy=True)
    calls: list[ToolCall] = []
    result = await adapter.run(request(tmp_path), on_tool_call=calls.append)
    assert result.harness == adapter.name == name
    assert result.harness_version == adapter.version() and result.harness_version
    assert result.completed and result.outcome == "completed"
    assert isinstance(result.session_id, str) and result.session_id
    assert result.envelope_text is not None and isinstance(json.loads(result.envelope_text), dict)
    usage = result.usage
    assert all(
        isinstance(v, int) and v >= 0
        for v in (
            usage.input_tokens,
            usage.output_tokens,
            usage.cache_read_tokens,
            usage.cache_write_tokens,
        )
    )
    assert usage.cost_usd >= 0.0 and result.exit_code == 0 and result.retries >= 0
    assert calls, "every adapter streams at least one tool call for the trace"
    for call in calls:
        assert call.tool and call.tool_call_id
        assert call.started_at and call.ended_at
        assert call.duration_ms is not None and call.duration_ms >= 0
        assert call.ok in (True, False, None)


@pytest.mark.parametrize("name", ADAPTERS)
async def test_timeout_meets_the_contract(name: str, tmp_path: Path) -> None:
    adapter = build(name, healthy=False)
    result = await adapter.run(
        request(tmp_path, timeout_s=1, resume="keep"), on_tool_call=lambda _: None
    )
    assert result.harness == name and result.outcome == "timeout" and not result.completed
    assert result.envelope_text is None and result.error
