"""The ``fake`` harness: replays recorded scripts so the loop runs without a model.

A script carries the tool calls to stream, the file effects to apply in the
workspace, the envelope to return and the usage to report. ``sleep_s`` runs
a real sleeping subprocess through the same runner the real adapters use,
so timeouts and kills are exercised for real.
"""

import copy
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

from orchestrator import constants
from orchestrator.clock import utc_now
from orchestrator.errors import HarnessError
from orchestrator.harness.base import (
    HarnessRequest,
    HarnessResult,
    ProcessSink,
    ToolCall,
    ToolCallSink,
    Usage,
)
from orchestrator.harness.claude_code import Runner
from orchestrator.harness.process import run_with_timeout

FAKE_VERSION = "fake-0.1"
HOOKED_TOOLS = frozenset({"Bash", "Write", "Edit", "MultiEdit", "NotebookEdit"})


class FakeToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: str
    args: object = None
    result_snippet: str | None = None
    ok: bool | None = True
    duration_ms: int | None = None
    tool_call_id: str | None = None


class FakeEffect(BaseModel):
    """A file change the fake builder makes in the workspace."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    write: str | None = None
    content: str = ""
    delete: str | None = None


class FakeUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0


class FakeScript(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str | None = None
    harness_version: str = FAKE_VERSION
    tool_calls: tuple[FakeToolCall, ...] = ()
    effects: tuple[FakeEffect, ...] = ()
    envelope: dict[str, object] | None = None
    envelope_text: str | None = Field(default=None, description="raw text when envelope is None")
    usage: FakeUsage = FakeUsage()
    sleep_s: float = 0.0
    exit_code: int = 0
    fail_with: str | None = Field(default=None, description="simulate a harness error")

    @classmethod
    def from_file(cls, path: Path) -> "FakeScript":
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def envelope_as_text(self) -> str | None:
        if self.envelope is not None:
            return json.dumps(self.envelope, sort_keys=True)
        return self.envelope_text


def _safe_relative(cwd: Path, raw: str) -> Path:
    rel = PurePosixPath(raw)
    if rel.is_absolute() or ".." in rel.parts or raw.startswith(("/", "\\")) or ":" in raw:
        raise HarnessError(f"fake effect path must stay inside the workspace: {raw}")
    return cwd / Path(*rel.parts)


def apply_effects(cwd: Path, effects: Sequence[FakeEffect]) -> list[str]:
    """Apply write and delete effects under ``cwd``; return the touched relative paths."""
    touched: list[str] = []
    for effect in effects:
        if effect.write is not None:
            target = _safe_relative(cwd, effect.write)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(effect.content, encoding="utf-8", newline="\n")
            touched.append(effect.write)
        if effect.delete is not None:
            target = _safe_relative(cwd, effect.delete)
            if target.exists():
                target.unlink()
            touched.append(effect.delete)
    return touched


class FakeHarness:
    name = "fake"

    def __init__(
        self,
        scripts: Sequence[FakeScript],
        *,
        python: str = sys.executable,
        runner: Runner = run_with_timeout,
    ) -> None:
        if not scripts:
            raise HarnessError("the fake harness needs at least one script")
        self._scripts = list(scripts)
        self._position = [0]  # a list so with_runner copies share one cursor
        self._python = python
        self._runner = runner
        self.calls: list[HarnessRequest] = []

    def with_runner(self, runner: Runner) -> "FakeHarness":
        """A copy that runs its subprocess through ``runner`` (a task container)."""
        clone = copy.copy(self)
        clone._runner = runner
        return clone

    @classmethod
    def from_fixtures(cls, *paths: Path) -> "FakeHarness":
        return cls([FakeScript.from_file(p) for p in paths])

    def version(self) -> str:
        return FAKE_VERSION

    async def _hook(
        self, request: HarnessRequest, hooks_dir: str, call: FakeToolCall
    ) -> str | None:
        """Run the real PreToolUse hook as Claude Code would; the denial text or ``None``.

        The hook reads its workspace from the process cwd, which the runner sets
        (``/work`` inside a container), so the payload carries no ``cwd``.
        """
        script = f"{hooks_dir.replace('\\', '/').rstrip('/')}/{constants.HOOK_SCRIPT}"
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": call.tool,
            "tool_input": call.args if isinstance(call.args, dict) else {},
        }
        outcome = await self._runner(
            [request.hook_python, script],
            cwd=request.cwd,
            env=request.env,
            timeout_s=constants.HOOK_TIMEOUT_S * 2,
            stdin_text=json.dumps(payload),
        )
        if outcome.exit_code == 2 and constants.HOOK_DENIAL_PREFIX in outcome.stderr_tail:
            reason = outcome.stderr_tail.strip().splitlines()[-1]
            return f"PreToolUse:{call.tool} hook error: [{script}]: {reason}"
        if outcome.exit_code != 0:
            raise HarnessError(
                f"the PreToolUse hook failed in the fake replay (exit {outcome.exit_code}): "
                f"{outcome.stderr_tail[-200:]}"
            )
        return None

    def next_script(self) -> FakeScript:
        script = self._scripts[min(self._position[0], len(self._scripts) - 1)]
        self._position[0] += 1
        return script

    async def run(
        self,
        request: HarnessRequest,
        *,
        on_tool_call: ToolCallSink,
        on_process: ProcessSink | None = None,
    ) -> HarnessResult:
        self.calls.append(request)
        script = self.next_script()
        session_id = (
            script.session_id or request.resume_session_id or f"fake-{self._position[0]:04d}"
        )
        if script.fail_with is not None:
            return HarnessResult(
                harness=self.name,
                harness_version=script.harness_version,
                outcome="error",
                session_id=session_id,
                error=script.fail_with,
            )
        apply_effects(request.cwd, script.effects)
        for number, call in enumerate(script.tool_calls, start=1):
            started = utc_now()
            snippet, ok, denied = call.result_snippet, call.ok, False
            if request.hooks_dir is not None and call.tool in HOOKED_TOOLS:
                refusal = await self._hook(request, request.hooks_dir, call)
                if refusal is not None:
                    snippet, ok, denied = refusal, False, True
            on_tool_call(
                ToolCall(
                    tool=call.tool,
                    tool_call_id=call.tool_call_id or f"{session_id}-{number}",
                    args=call.args,
                    result_snippet=snippet,
                    ok=ok,
                    duration_ms=call.duration_ms if call.duration_ms is not None else 0,
                    started_at=started,
                    ended_at=utc_now(),
                    denied=denied,
                )
            )
        exit_code = script.exit_code
        if script.sleep_s > 0:
            outcome = await self._runner(
                [self._python, "-c", f"import time; time.sleep({script.sleep_s})"],
                cwd=request.cwd,
                env=request.env,
                timeout_s=request.timeout_s,
                on_process=on_process,
            )
            if outcome.timed_out:
                return HarnessResult(
                    harness=self.name,
                    harness_version=script.harness_version,
                    outcome="timeout",
                    session_id=session_id,
                    exit_code=outcome.exit_code,
                    stderr_tail=outcome.stderr_tail,
                    error=f"timed out after {request.timeout_s} s",
                )
            exit_code = outcome.exit_code if outcome.exit_code is not None else exit_code
        usage = Usage(
            input_tokens=script.usage.input_tokens,
            output_tokens=script.usage.output_tokens,
            cache_read_tokens=script.usage.cache_read_tokens,
            cache_write_tokens=script.usage.cache_write_tokens,
            cost_usd=script.usage.cost_usd,
        )
        return HarnessResult(
            harness=self.name,
            harness_version=script.harness_version,
            outcome="completed",
            envelope_text=script.envelope_as_text(),
            session_id=session_id,
            usage=usage,
            exit_code=exit_code,
        )


ISSUE_IN_PROMPT = re.compile(r"specs/(\d+)-|^# (\d+) ", re.MULTILINE)


def issue_from_prompt(prompt: str) -> int | None:
    match = ISSUE_IN_PROMPT.search(prompt)
    if match is None:
        return None
    return int(match.group(1) or match.group(2))


class KeyedFakeHarness(FakeHarness):
    """Scripts chosen by role and the issue number in the prompt: one process, many tasks."""

    def __init__(
        self,
        scripts: Mapping[tuple[str, int], Sequence[FakeScript]],
        *,
        defaults: Mapping[str, FakeScript] | None = None,
        python: str = sys.executable,
    ) -> None:
        seed = [s[0] for s in scripts.values() if s] or list((defaults or {}).values())
        super().__init__(seed, python=python)
        self._keyed = {key: list(value) for key, value in scripts.items() if value}
        self._defaults = dict(defaults or {})
        self._cursor: dict[tuple[str, int], int] = {}
        self.picked: list[tuple[str, int | None]] = []

    @classmethod
    def from_seed_dir(cls, scripts_dir: Path) -> "KeyedFakeHarness":
        """``<n>.json`` for the builder, ``planner/<n>.json``, ``reviewer/<n>.json``."""
        scripts: dict[tuple[str, int], list[FakeScript]] = {}
        layout = {
            "builder": scripts_dir,
            "planner": scripts_dir / "planner",
            "reviewer": scripts_dir / "reviewer",
        }
        for role, folder in layout.items():
            if not folder.is_dir():
                continue
            for file in sorted(folder.glob("*.json")):
                stem = file.stem.split("-", 1)[0]
                if stem.isdigit():
                    scripts.setdefault((role, int(stem)), []).append(FakeScript.from_file(file))
        if not scripts:
            raise HarnessError(f"no fake scripts under {scripts_dir}")
        return cls(scripts)

    def pick(self, request: HarnessRequest) -> FakeScript:
        issue = issue_from_prompt(request.prompt)
        role = str(request.role)
        self.picked.append((role, issue))
        if issue is not None and (role, issue) in self._keyed:
            scripts = self._keyed[(role, issue)]
            index = self._cursor.get((role, issue), 0)
            self._cursor[(role, issue)] = index + 1
            return scripts[min(index, len(scripts) - 1)]
        if role in self._defaults:
            return self._defaults[role]
        raise HarnessError(f"no fake script for role {role!r} and issue {issue!r}")

    async def run(
        self,
        request: HarnessRequest,
        *,
        on_tool_call: ToolCallSink,
        on_process: ProcessSink | None = None,
    ) -> HarnessResult:
        self._scripts = [self.pick(request)]
        self._position = [0]  # a list so with_runner copies share one cursor
        return await super().run(request, on_tool_call=on_tool_call, on_process=on_process)
