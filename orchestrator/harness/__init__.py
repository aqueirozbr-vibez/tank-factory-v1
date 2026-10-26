"""Harness adapters: one contract over Claude Code, Codex and the fake replay.

Prompt and schema in; envelope, session id and usage out; resume by session
id; the process tree dies on timeout (PRD §Capability 3 · Harness adapter).
"""

from orchestrator.harness.base import (
    HarnessAdapter,
    HarnessRequest,
    HarnessResult,
    ProcessSink,
    ToolCall,
    ToolCallSink,
    Usage,
)
from orchestrator.harness.claude_code import ClaudeCodeHarness
from orchestrator.harness.codex import CodexHarness
from orchestrator.harness.fake import FakeHarness, FakeScript
from orchestrator.harness.sink import trace_tool_call_sink

__all__ = [
    "ClaudeCodeHarness",
    "CodexHarness",
    "FakeHarness",
    "FakeScript",
    "HarnessAdapter",
    "HarnessRequest",
    "HarnessResult",
    "ProcessSink",
    "ToolCall",
    "ToolCallSink",
    "Usage",
    "trace_tool_call_sink",
]
