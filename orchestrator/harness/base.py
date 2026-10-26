"""The harness adapter contract (TRD §2.0 · Harness adapter mechanism)."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from orchestrator.models.enums import AgentRole

Outcome = Literal["completed", "timeout", "killed", "error"]


@dataclass(frozen=True)
class HarnessRequest:
    """Everything a harness call receives. Nothing else reaches the agent."""

    role: AgentRole
    prompt: str
    output_schema: Mapping[str, object]
    cwd: Path
    env: Mapping[str, str]
    model: str
    timeout_s: int
    system_prompt: str | None = None
    tools: tuple[str, ...] = ()
    thinking: str | None = None
    max_turns: int | None = None
    budget_usd: float | None = None
    resume_session_id: str | None = None
    hooks_dir: str | None = None  # the PreToolUse bundle as the harness process sees it
    hook_python: str = "python3"


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0


@dataclass(frozen=True)
class ToolCall:
    """One streamed tool call, the unit of the trace (TRD §9)."""

    tool: str
    tool_call_id: str
    args: object
    result_snippet: str | None
    ok: bool | None
    duration_ms: int | None
    started_at: str | None = None
    ended_at: str | None = None
    denied: bool = False  # refused by the PreToolUse hook (TRD §7.3)


@dataclass(frozen=True)
class HarnessResult:
    harness: str
    harness_version: str
    outcome: Outcome
    envelope_text: str | None = None
    session_id: str | None = None
    usage: Usage = field(default_factory=Usage)
    exit_code: int | None = None
    stderr_tail: str = ""
    error: str | None = None
    num_turns: int | None = None
    retries: int = 0
    # The reported cost is the whole session's running total (Claude Code's total_cost_usd),
    # so a resumed session's call repeats what earlier phases already booked.
    cost_is_session_total: bool = False

    @property
    def completed(self) -> bool:
        return self.outcome == "completed"


ToolCallSink = Callable[[ToolCall], None]
ProcessSink = Callable[[int], None]


class HarnessAdapter(Protocol):
    name: str

    def version(self) -> str:
        """The harness version read from the binary, recorded on every phase row."""
        ...

    async def run(
        self,
        request: HarnessRequest,
        *,
        on_tool_call: ToolCallSink,
        on_process: ProcessSink | None = None,
    ) -> HarnessResult: ...
