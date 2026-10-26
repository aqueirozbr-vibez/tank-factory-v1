"""Streamed tool calls become ``tool_call`` events as the agent runs (TRD §9)."""

from orchestrator import constants
from orchestrator.harness.base import ToolCall, ToolCallSink
from orchestrator.models.enums import EventKind
from orchestrator.trace.writer import TraceWriter


def trace_tool_call_sink(
    writer: TraceWriter,
    *,
    task_id: str,
    run_id: str,
    phase_id: str,
    actor: str,
    process_id: str | None = None,
) -> ToolCallSink:
    """A sink that writes one event per tool call and keeps the process row fresh."""

    def sink(call: ToolCall) -> None:
        snippet = call.result_snippet
        if snippet is not None and len(snippet) > constants.RESULT_SNIPPET_CHARS:
            snippet = snippet[: constants.RESULT_SNIPPET_CHARS]
        writer.write_event(
            EventKind.TOOL_CALL,
            actor,
            task_id=task_id,
            run_id=run_id,
            phase_id=phase_id,
            payload={
                "tool": call.tool,
                "tool_call_id": call.tool_call_id,
                "args": call.args,
                "result_snippet": snippet,
                "ok": call.ok,
                "duration_ms": call.duration_ms,
                "denied": call.denied,
                "agent": actor,
            },
            started_at=call.started_at,
            ended_at=call.ended_at,
        )
        if process_id is not None:
            writer.touch_process(process_id)

    return sink
