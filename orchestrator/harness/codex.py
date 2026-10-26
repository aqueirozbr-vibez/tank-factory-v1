"""Codex CLI as a subprocess with JSONL events (TRD §2.0, PRD §Capability 3, 4).

Flags and event shapes follow the CLI reference, the non-interactive guide
and ``codex-rs/exec/src/exec_events.rs`` as of 2026-10-24; see unit spec 26.
The stream fixtures are hand-authored from that schema until a real run
records them.
"""

import json
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from orchestrator.clock import utc_now
from orchestrator.errors import HarnessError
from orchestrator.harness.base import (
    HarnessRequest,
    HarnessResult,
    Outcome,
    ProcessSink,
    ToolCall,
    ToolCallSink,
    Usage,
)
from orchestrator.harness.claude_code import SIGTERM_EXIT, Runner
from orchestrator.harness.process import ProcessOutcome, run_with_timeout
from orchestrator.models.enums import AgentRole

TOOL_ITEMS = frozenset({"command_execution", "file_change", "mcp_tool_call", "web_search"})
READ_ONLY_ROLES = frozenset({AgentRole.REVIEWER})
SCHEMA_FILE = "output_schema.json"


def strip_fences(text: str) -> str:
    """``\\`\\`\\`json ... \\`\\`\\``` around a final message → the inner text."""
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        body = stripped[3:-3]
        first_newline = body.find("\n")
        if first_newline != -1 and body[:first_newline].strip().isalpha():
            body = body[first_newline + 1 :]
        return body.strip()
    return stripped


def _tool_name(item: Mapping[str, object]) -> str:
    kind = item.get("type")
    if kind == "command_execution":
        return "shell"
    if kind == "file_change":
        return "apply_patch"
    if kind == "mcp_tool_call":
        return f"mcp:{item.get('server', '?')}.{item.get('tool', '?')}"
    return "web_search"


def _tool_args(item: Mapping[str, object]) -> object:
    kind = item.get("type")
    if kind == "command_execution":
        return {"command": item.get("command")}
    if kind == "file_change":
        return {"changes": item.get("changes")}
    if kind == "mcp_tool_call":
        return item.get("arguments")
    return {"query": item.get("query")}


def _tool_outcome(item: Mapping[str, object]) -> tuple[str | None, bool | None]:
    """The result snippet and success flag of a completed tool item."""
    kind = item.get("type")
    status = item.get("status")
    if kind == "command_execution":
        exit_code = item.get("exit_code")
        output = item.get("aggregated_output")
        ok = status == "completed" and (exit_code is None or exit_code == 0)
        return (str(output) if output is not None else None), ok
    if kind == "file_change":
        changes = item.get("changes")
        lines = (
            [f"{c.get('kind')} {c.get('path')}" for c in changes if isinstance(c, dict)]
            if isinstance(changes, list)
            else []
        )
        return "\n".join(lines) or None, status == "completed"
    if kind == "mcp_tool_call":
        error = item.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"]), False
        result = item.get("result")
        snippet: str | None = None
        if isinstance(result, dict):
            content = result.get("content")
            texts = (
                [str(b.get("text", "")) for b in content if isinstance(b, dict)]
                if isinstance(content, list)
                else []
            )
            snippet = "\n".join(t for t in texts if t) or json.dumps(result, sort_keys=True)
        return snippet, status == "completed"
    return None, True


class CodexStreamParser:
    """Turns ``codex exec --json`` lines into tool calls and keeps the final message."""

    def __init__(self, on_tool_call: ToolCallSink) -> None:
        self._on_tool_call = on_tool_call
        self.thread_id: str | None = None
        self.turns_started = 0
        self.turns_completed = 0
        self.usage = Usage()
        self.failure: str | None = None
        self.errors: list[str] = []
        self.last_message: str | None = None
        self.unparsed_lines = 0
        self.tool_calls = 0
        self._pending: dict[str, tuple[str, object, float, str]] = {}

    def feed(self, line: str) -> None:
        text = line.strip()
        if not text:
            return
        try:
            event = json.loads(text)
        except ValueError:
            self.unparsed_lines += 1
            return
        if not isinstance(event, dict):
            self.unparsed_lines += 1
            return
        kind = event.get("type")
        if kind == "thread.started":
            thread_id = event.get("thread_id")
            if isinstance(thread_id, str):
                self.thread_id = thread_id
        elif kind == "turn.started":
            self.turns_started += 1
        elif kind == "turn.completed":
            self.turns_completed += 1
            self._add_usage(event.get("usage"))
        elif kind == "turn.failed":
            error = event.get("error")
            message = error.get("message") if isinstance(error, dict) else error
            self.failure = str(message or "turn failed")
        elif kind == "error":
            self.errors.append(str(event.get("message", "error")))
        elif kind in ("item.started", "item.updated", "item.completed"):
            item = event.get("item")
            if isinstance(item, dict):
                self._item(str(kind), item)

    def finish(self) -> None:
        """Flush tool items whose completion never arrived."""
        for item_id, (name, args, started, started_at) in list(self._pending.items()):
            self._emit(name, item_id, args, None, None, started, started_at)
        self._pending.clear()

    def _add_usage(self, raw: object) -> None:
        usage = raw if isinstance(raw, dict) else {}

        def _int(key: str) -> int:
            value = usage.get(key, 0)
            return int(value) if isinstance(value, int | float) else 0

        self.usage = Usage(
            input_tokens=self.usage.input_tokens + _int("input_tokens"),
            output_tokens=self.usage.output_tokens + _int("output_tokens"),
            cache_read_tokens=self.usage.cache_read_tokens + _int("cached_input_tokens"),
            cache_write_tokens=self.usage.cache_write_tokens + _int("cache_write_input_tokens"),
            cost_usd=0.0,
        )

    def _item(self, kind: str, item: dict[str, object]) -> None:
        item_type = item.get("type")
        item_id = str(item.get("id", ""))
        if item_type == "agent_message":
            if kind == "item.completed":
                self.last_message = strip_fences(str(item.get("text", "")))
            return
        if item_type == "error":
            if kind == "item.completed":
                self.errors.append(str(item.get("message", "error")))
            return
        if item_type not in TOOL_ITEMS or kind == "item.updated":
            return
        if kind == "item.started":
            self._pending[item_id] = (
                _tool_name(item),
                _tool_args(item),
                time.monotonic(),
                utc_now(),
            )
            return
        pending = self._pending.pop(item_id, None)
        if pending is None:
            name, args, started, started_at = (
                _tool_name(item),
                _tool_args(item),
                time.monotonic(),
                utc_now(),
            )
        else:
            name, args, started, started_at = pending
        snippet, ok = _tool_outcome(item)
        self._emit(name, item_id, args, snippet, ok, started, started_at)

    def _emit(
        self,
        name: str,
        item_id: str,
        args: object,
        snippet: str | None,
        ok: bool | None,
        started: float,
        started_at: str,
    ) -> None:
        self.tool_calls += 1
        self._on_tool_call(
            ToolCall(
                tool=name,
                tool_call_id=item_id,
                args=args,
                result_snippet=snippet,
                ok=ok,
                duration_ms=int((time.monotonic() - started) * 1000),
                started_at=started_at,
                ended_at=utc_now(),
            )
        )


def parse_codex_version(text: str) -> str:
    """``codex-cli 0.112.0`` → ``0.112.0``."""
    for token in text.split():
        candidate = token.lstrip("v")
        if candidate[:1].isdigit():
            return candidate
    raise HarnessError(f"could not read the Codex version from {text!r}")


class CodexHarness:
    name = "codex"

    def __init__(
        self,
        binary: str = "codex",
        *,
        runner: Runner = run_with_timeout,
        version_probe: Callable[[], str] | None = None,
    ) -> None:
        self.binary = binary
        self._runner = runner
        self._version_probe = version_probe
        self._version: str | None = None

    # --- version ------------------------------------------------------------

    def version(self) -> str:
        if self._version is None:
            if self._version_probe is not None:
                self._version = parse_codex_version(self._version_probe())
            else:
                try:
                    completed = subprocess.run(
                        [self.binary, "--version"],
                        capture_output=True,
                        text=True,
                        timeout=60,
                        check=False,
                    )
                except (OSError, subprocess.TimeoutExpired) as exc:
                    raise HarnessError(
                        f"Codex binary {self.binary!r} did not answer --version: {exc}"
                    ) from exc
                self._version = parse_codex_version(completed.stdout or completed.stderr)
        return self._version

    # --- argv and prompt ----------------------------------------------------

    @staticmethod
    def sandbox_for(role: AgentRole) -> str:
        return "read-only" if role in READ_ONLY_ROLES else "workspace-write"

    @staticmethod
    def compose_prompt(request: HarnessRequest) -> str:
        """The CLI has no system-prompt flag; the guide goes above the task."""
        if request.system_prompt is None:
            return request.prompt
        return f"{request.system_prompt}\n\n---\n\n{request.prompt}"

    def build_argv(self, request: HarnessRequest, *, schema_path: Path) -> list[str]:
        argv = [
            self.binary,
            "exec",
            "--json",
            "--color",
            "never",
            "--skip-git-repo-check",
            "--sandbox",
            self.sandbox_for(request.role),
            "-C",
            str(request.cwd),
            "-m",
            request.model,
            "--output-schema",
            str(schema_path),
            "-c",
            'approval_policy="never"',
            "-c",
            "sandbox_workspace_write.network_access=false",
        ]
        if request.thinking is not None:
            argv += ["-c", f'model_reasoning_effort="{request.thinking}"']
        if request.resume_session_id is not None:
            argv += ["resume", request.resume_session_id]
        argv.append("-")
        return argv

    # --- run ----------------------------------------------------------------

    async def run(
        self,
        request: HarnessRequest,
        *,
        on_tool_call: ToolCallSink,
        on_process: ProcessSink | None = None,
    ) -> HarnessResult:
        parser = CodexStreamParser(on_tool_call)
        with tempfile.TemporaryDirectory(prefix="factory-codex-") as scratch:
            schema_path = Path(scratch) / SCHEMA_FILE
            schema_path.write_text(
                json.dumps(request.output_schema, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            outcome = await self._runner(
                self.build_argv(request, schema_path=schema_path),
                cwd=request.cwd,
                env=request.env,
                timeout_s=request.timeout_s,
                on_stdout_line=parser.feed,
                on_process=on_process,
                stdin_text=self.compose_prompt(request),
            )
        parser.finish()
        return self._to_result(request, parser, outcome)

    def _to_result(
        self, request: HarnessRequest, parser: CodexStreamParser, outcome: ProcessOutcome
    ) -> HarnessResult:
        try:
            version = self.version()  # the stream carries no version line
        except HarnessError:
            version = "unknown"
        session_id = parser.thread_id or request.resume_session_id

        def make(
            kind: Outcome,
            *,
            envelope_text: str | None = None,
            error: str | None = None,
            retries: int = 0,
        ) -> HarnessResult:
            return HarnessResult(
                harness=self.name,
                harness_version=version,
                outcome=kind,
                envelope_text=envelope_text,
                session_id=session_id,
                usage=parser.usage,
                exit_code=outcome.exit_code,
                stderr_tail=outcome.stderr_tail,
                error=error,
                num_turns=parser.turns_started or None,
                retries=retries,
            )

        if outcome.timed_out:
            return make("timeout", error=f"timed out after {request.timeout_s} s")
        if parser.failure is not None:
            return make("error", error=f"turn_failed: {parser.failure}")
        if parser.turns_completed == 0:
            if parser.errors:
                return make("error", error="; ".join(parser.errors))
            killed = outcome.exit_code in (None, SIGTERM_EXIT)
            tail = outcome.stderr_tail[-300:]
            detail = f"no turn.completed (exit code {outcome.exit_code})"
            return make(
                "killed" if killed else "error", error=f"{detail}: {tail}" if tail else detail
            )
        if outcome.exit_code not in (0, None):
            detail = "; ".join(parser.errors) or f"exit code {outcome.exit_code}"
            return make("error", error=detail, retries=len(parser.errors))
        return make("completed", envelope_text=parser.last_message, retries=len(parser.errors))
