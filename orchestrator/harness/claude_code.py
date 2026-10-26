"""Claude Code as a CLI subprocess with streamed JSON (TRD §2.0, PRD §Capability 3).

Flags and message shapes follow the CLI reference and headless docs as of
2026-10-24 and the installed binary 2.1.309; see unit spec 06.
"""

import json
import re
import subprocess
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol

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
from orchestrator.harness.process import ProcessOutcome, run_with_timeout
from orchestrator.workspace.hooks import settings_json

SIGTERM_EXIT = 143
HOOK_DENIAL = re.compile(r"^PreToolUse:[A-Za-z_]+ hook error")
RESULT_ERROR_SUBTYPES = frozenset(
    {
        "error_max_turns",
        "error_during_execution",
        "error_max_budget_usd",
        "error_max_structured_output_retries",
    }
)


class Runner(Protocol):
    def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_s: float,
        on_stdout_line: Callable[[str], None] | None = None,
        on_process: Callable[[int], None] | None = None,
        stdin_text: str | None = None,
    ) -> Awaitable[ProcessOutcome]: ...


def _text_of(content: object) -> str | None:
    """The text of a tool_result ``content`` (a string or a list of blocks)."""
    if content is None:
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "\n".join(parts)
    return json.dumps(content, sort_keys=True)


class StreamParser:
    """Turns ``stream-json`` lines into tool calls and keeps the result message."""

    def __init__(self, on_tool_call: ToolCallSink) -> None:
        self._on_tool_call = on_tool_call
        self.session_id: str | None = None
        self.model: str | None = None
        self.claude_code_version: str | None = None
        self.result: dict[str, object] | None = None
        self.api_retries = 0
        self.unparsed_lines = 0
        self.tool_calls = 0
        self._pending: dict[str, tuple[str, object, float, str]] = {}

    def feed(self, line: str) -> None:
        text = line.strip()
        if not text:
            return
        try:
            message = json.loads(text)
        except ValueError:
            self.unparsed_lines += 1
            return
        if not isinstance(message, dict):
            self.unparsed_lines += 1
            return
        kind = message.get("type")
        if kind == "system":
            self._system(message)
        elif kind == "assistant":
            self._assistant(message)
        elif kind == "user":
            self._user(message)
        elif kind == "result":
            self.result = message
            session = message.get("session_id")
            if isinstance(session, str):
                self.session_id = session

    def finish(self) -> None:
        """Flush tool calls whose result never arrived."""
        for tool_use_id, (name, args, started, started_at) in list(self._pending.items()):
            self._emit(name, tool_use_id, args, None, None, started, started_at)
        self._pending.clear()

    def _system(self, message: dict[str, object]) -> None:
        subtype = message.get("subtype")
        if subtype == "init":
            session = message.get("session_id")
            if isinstance(session, str):
                self.session_id = session
            model = message.get("model")
            if isinstance(model, str):
                self.model = model
            version = message.get("claude_code_version")
            if isinstance(version, str):
                self.claude_code_version = version
        elif subtype == "api_retry":
            self.api_retries += 1

    @staticmethod
    def _blocks(message: dict[str, object]) -> list[dict[str, object]]:
        inner = message.get("message")
        if not isinstance(inner, dict):
            return []
        content = inner.get("content")
        if not isinstance(content, list):
            return []
        return [block for block in content if isinstance(block, dict)]

    def _assistant(self, message: dict[str, object]) -> None:
        for block in self._blocks(message):
            if block.get("type") != "tool_use":
                continue
            tool_use_id = str(block.get("id", ""))
            name = str(block.get("name", "unknown"))
            self._pending[tool_use_id] = (name, block.get("input"), time.monotonic(), utc_now())

    def _user(self, message: dict[str, object]) -> None:
        for block in self._blocks(message):
            if block.get("type") != "tool_result":
                continue
            tool_use_id = str(block.get("tool_use_id", ""))
            pending = self._pending.pop(tool_use_id, None)
            if pending is None:
                name, args, started, started_at = "unknown", None, time.monotonic(), utc_now()
            else:
                name, args, started, started_at = pending
            is_error = bool(block.get("is_error", False))
            self._emit(
                name,
                tool_use_id,
                args,
                _text_of(block.get("content")),
                not is_error,
                started,
                started_at,
            )

    def _emit(
        self,
        name: str,
        tool_use_id: str,
        args: object,
        snippet: str | None,
        ok: bool | None,
        started: float,
        started_at: str,
    ) -> None:
        self.tool_calls += 1
        denied = ok is False and snippet is not None and HOOK_DENIAL.match(snippet) is not None
        self._on_tool_call(
            ToolCall(
                tool=name,
                tool_call_id=tool_use_id,
                args=args,
                result_snippet=snippet,
                ok=ok,
                duration_ms=int((time.monotonic() - started) * 1000),
                started_at=started_at,
                ended_at=utc_now(),
                denied=denied,
            )
        )


def _usage_from(result: Mapping[str, object]) -> Usage:
    raw = result.get("usage")
    usage = raw if isinstance(raw, dict) else {}

    def _int(key: str) -> int:
        value = usage.get(key, 0)
        return int(value) if isinstance(value, int | float) else 0

    cost = result.get("total_cost_usd", 0.0)
    return Usage(
        input_tokens=_int("input_tokens"),
        output_tokens=_int("output_tokens"),
        cache_read_tokens=_int("cache_read_input_tokens"),
        cache_write_tokens=_int("cache_creation_input_tokens"),
        cost_usd=float(cost) if isinstance(cost, int | float) else 0.0,
    )


def parse_version(text: str) -> str:
    """``2.1.309 (Claude Code)`` → ``2.1.309``."""
    token = text.strip().split()[0] if text.strip() else ""
    if not token or not token[0].isdigit():
        raise HarnessError(f"could not read the Claude Code version from {text!r}")
    return token


class ClaudeCodeHarness:
    name = "claude_code"

    def __init__(
        self,
        binary: str = "claude",
        *,
        bare: bool = True,
        runner: Runner = run_with_timeout,
        version_probe: Callable[[], str] | None = None,
    ) -> None:
        self.binary = binary
        self.bare = bare
        self._runner = runner
        self._version_probe = version_probe
        self._version: str | None = None

    # --- version ------------------------------------------------------------

    def version(self) -> str:
        if self._version is None:
            if self._version_probe is not None:
                self._version = parse_version(self._version_probe())
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
                        f"Claude Code binary {self.binary!r} did not answer --version: {exc}"
                    ) from exc
                self._version = parse_version(completed.stdout or completed.stderr)
        return self._version

    # --- argv ---------------------------------------------------------------

    def build_argv(self, request: HarnessRequest) -> list[str]:
        argv = [self.binary, "-p"]
        if self.bare:
            argv.append("--bare")
        argv += [
            "--output-format",
            "stream-json",
            "--verbose",
            "--json-schema",
            json.dumps(request.output_schema, sort_keys=True, separators=(",", ":")),
            "--model",
            request.model,
            "--permission-mode",
            "dontAsk",
            "--permission-prompts",
            "none",
        ]
        if request.max_turns is not None:
            argv += ["--max-turns", str(request.max_turns)]
        if request.budget_usd is not None:
            argv += ["--max-budget-usd", f"{request.budget_usd:.2f}"]
        if request.tools:
            tools = ",".join(request.tools)
            argv += ["--tools", tools, "--allowedTools", tools]
        if request.hooks_dir is not None:
            document = settings_json(request.hooks_dir, request.hook_python, compact=True)
            argv += ["--settings", document]
        if request.system_prompt is not None:
            argv += ["--system-prompt", request.system_prompt]
        if request.resume_session_id is not None:
            argv += ["--resume", request.resume_session_id]
        return argv

    # --- run ----------------------------------------------------------------

    async def run(
        self,
        request: HarnessRequest,
        *,
        on_tool_call: ToolCallSink,
        on_process: ProcessSink | None = None,
    ) -> HarnessResult:
        parser = StreamParser(on_tool_call)
        outcome = await self._runner(
            self.build_argv(request),
            cwd=request.cwd,
            env=request.env,
            timeout_s=request.timeout_s,
            on_stdout_line=parser.feed,
            on_process=on_process,
            stdin_text=request.prompt,
        )
        parser.finish()
        return self._to_result(request, parser, outcome)

    def _to_result(
        self, request: HarnessRequest, parser: StreamParser, outcome: ProcessOutcome
    ) -> HarnessResult:
        version = parser.claude_code_version or self._version or "unknown"
        session_id = parser.session_id or request.resume_session_id

        def make(
            kind: Outcome,
            *,
            envelope_text: str | None = None,
            usage: Usage | None = None,
            error: str | None = None,
            num_turns: int | None = None,
        ) -> HarnessResult:
            return HarnessResult(
                harness=self.name,
                harness_version=version,
                outcome=kind,
                cost_is_session_total=True,
                envelope_text=envelope_text,
                session_id=session_id,
                usage=usage or Usage(),
                exit_code=outcome.exit_code,
                stderr_tail=outcome.stderr_tail,
                error=error,
                num_turns=num_turns,
                retries=parser.api_retries,
            )

        if outcome.timed_out:
            return make("timeout", error=f"timed out after {request.timeout_s} s")
        result = parser.result
        if result is None:
            killed = outcome.exit_code in (None, SIGTERM_EXIT)
            tail = outcome.stderr_tail[-300:]
            detail = f"no result message (exit code {outcome.exit_code})"
            return make(
                "killed" if killed else "error", error=f"{detail}: {tail}" if tail else detail
            )
        usage = _usage_from(result)
        num_turns_raw = result.get("num_turns")
        num_turns = int(num_turns_raw) if isinstance(num_turns_raw, int) else None
        subtype = str(result.get("subtype", ""))
        if subtype == "success" and not bool(result.get("is_error", False)):
            envelope_text = (
                json.dumps(result.get("structured_output"), sort_keys=True)
                if "structured_output" in result
                else None
            )
            return make("completed", envelope_text=envelope_text, usage=usage, num_turns=num_turns)
        errors_raw = result.get("errors")
        errors = [str(e) for e in errors_raw] if isinstance(errors_raw, list) else []
        if subtype == "success":
            errors.append(str(result.get("result", "run reported is_error")))
        detail = "; ".join(errors) if errors else "no detail"
        return make(
            "error", error=f"{subtype or 'error'}: {detail}", usage=usage, num_turns=num_turns
        )
