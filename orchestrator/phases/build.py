"""The build phase: one bounded builder, every claim checked by code (PRD §Cap. 3)."""

import asyncio
import re
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from orchestrator import constants
from orchestrator.budget import check_budget
from orchestrator.budget.keys import PhaseKeys
from orchestrator.budget.watch import run_guarded
from orchestrator.gates.base import GateContext, GateResult, write_gate
from orchestrator.gates.claims import claim_gates
from orchestrator.gates.envelope_schema import envelope_schema_gate, record_envelope
from orchestrator.gates.permissions import permissions_gate, record_permissions, take_snapshot
from orchestrator.gates.quality import run_quality
from orchestrator.harness.base import HarnessAdapter, HarnessRequest, HarnessResult, ToolCall
from orchestrator.harness.claude_code import Runner
from orchestrator.harness.process import run_with_timeout
from orchestrator.harness.sink import trace_tool_call_sink
from orchestrator.models.config import FactoryConfig
from orchestrator.models.enums import (
    AgentRole,
    BlockedReason,
    EventKind,
    OutputType,
    PhaseKind,
    PhaseName,
    PhaseStatus,
    ProcessKind,
    TaskStatus,
)
from orchestrator.models.envelopes import BuildOutput, Check, Finding, VerifyOutput
from orchestrator.models.settings import Settings
from orchestrator.prompt_builder import build_builder_prompt, load_guide
from orchestrator.trace.writer import TraceWriter
from orchestrator.version import factory_version
from orchestrator.workspace.git import run_git
from orchestrator.workspace.hooks import agent_env_with_hooks
from orchestrator.workspace.worktree import Worktree, commit_all

FOCUSED_TEST = re.compile(r"tests/[\w./\-]+(?:::[\w\[\]\-,]+)*")
Sleep = Callable[[float], Awaitable[None]]


def parse_focused_tests(spec_text: str) -> list[str]:
    """Test ids named in the spec's Test plan (PRD §Capability 2 · Test plan)."""
    parts = spec_text.split("## Test plan", 1)
    body = parts[1] if len(parts) == 2 else spec_text
    found = [m.group(0).rstrip(".,;)") for m in FOCUSED_TEST.finditer(body)]
    return [t for t in dict.fromkeys(found) if not t.startswith("tests/acceptance/")]


def verify_from(result: GateResult) -> VerifyOutput:
    """What the builder gets back after a failed gate: the evidence, not a verdict."""
    return VerifyOutput(
        status="fail",
        gate=str(result.gate),
        checks=[Check(item=c.item, ok=c.ok, note=c.note) for c in result.checks],
        violations=[f"{v.item}: {v.problem}" for v in result.violations],
        command=result.command,
        exit_code=result.exit_code,
        output_tail=result.output_tail or "",
    )


@dataclass
class BuildDeps:
    writer: TraceWriter
    config: FactoryConfig
    settings: Settings
    harness: HarnessAdapter
    sleep: Sleep = asyncio.sleep
    factory_version: str = field(default_factory=factory_version)
    runner: Runner = run_with_timeout
    keys: PhaseKeys = field(default_factory=PhaseKeys)
    hooks_dir: str | None = None  # the PreToolUse bundle as the agent process sees it
    hook_python: str = sys.executable


@dataclass(frozen=True)
class BuildOutcome:
    status: Literal["pass", "blocked"]
    run_id: str
    commit_sha: str | None
    blocked_reason: BlockedReason | None
    evidence: str
    repairs: int
    builder_calls: int
    cost_usd: float

    @property
    def passed(self) -> bool:
        return self.status == "pass"


class BuildPhase:
    """build → code_gates → commit for one task attempt."""

    def __init__(
        self,
        deps: BuildDeps,
        *,
        task_id: str,
        worktree: Worktree,
        spec_rel: str,
        run_id: str,
        reviewer_findings: Sequence[Finding] = (),
        seq_start: int | None = None,
        initial_verify: VerifyOutput | None = None,
        repairs_used: int = 0,
    ) -> None:
        self.deps = deps
        self.writer = deps.writer
        self.task_id = task_id
        self.worktree = worktree
        self.spec_rel = spec_rel
        self.run_id = run_id
        self.role = deps.config.roster.builder
        self.reviewer_findings = tuple(reviewer_findings)
        self.initial_verify = initial_verify
        self._seq = self.writer.last_phase_seq(run_id) if seq_start is None else seq_start
        self.cost_usd = 0.0
        self.builder_calls = 0
        self.repairs = repairs_used

    # --- small helpers --------------------------------------------------------

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _event(
        self, kind: EventKind, payload: object, *, phase_id: str | None, actor: str = "orchestrator"
    ) -> int:
        return self.writer.write_event(
            kind,
            actor,
            task_id=self.task_id,
            run_id=self.run_id,
            phase_id=phase_id,
            payload=payload,
        )

    def _start_phase(
        self,
        name: PhaseName,
        kind: PhaseKind,
        owner: str,
        *,
        attempt: int,
        timeout_s: int,
        **extra: object,
    ) -> str:
        self.writer.require_enabled()
        phase_id = self.writer.start_phase(
            run_id=self.run_id,
            seq=self._next_seq(),
            name=name,
            kind=kind,
            owner=owner,
            timeout_s=timeout_s,
            attempt=attempt,
            **extra,  # type: ignore[arg-type]
        )
        self._event(
            EventKind.PHASE_START, {"name": str(name), "attempt": attempt}, phase_id=phase_id
        )
        return phase_id

    def _finish_phase(
        self,
        phase_id: str,
        status: PhaseStatus,
        *,
        error: str | None = None,
        result: HarnessResult | None = None,
    ) -> None:
        if result is None:
            self.writer.finish_phase(phase_id, status, error=error)
        else:
            self.writer.finish_phase(
                phase_id,
                status,
                tokens_in=result.usage.input_tokens,
                tokens_out=result.usage.output_tokens,
                tokens_cache_read=result.usage.cache_read_tokens,
                tokens_cache_write=result.usage.cache_write_tokens,
                cost_usd=result.usage.cost_usd,
                retries=result.retries,
                session_id=result.session_id,
                error=error,
            )
        self._event(EventKind.PHASE_END, {"status": str(status), "error": error}, phase_id=phase_id)

    def _blocked(
        self, reason: BlockedReason, evidence: str, *, phase_id: str | None
    ) -> BuildOutcome:
        self.writer.set_task_status(self.task_id, TaskStatus.BLOCKED, blocked_reason=reason)
        self._event(
            EventKind.ERROR,
            {"blocked_reason": str(reason), "evidence": evidence[-constants.OUTPUT_TAIL_CHARS :]},
            phase_id=phase_id,
        )
        return BuildOutcome(
            "blocked",
            self.run_id,
            None,
            reason,
            evidence,
            self.repairs,
            self.builder_calls,
            self.cost_usd,
        )

    # --- the builder call ---------------------------------------------------------

    async def _call_builder(
        self,
        *,
        attempt: int,
        spec_text: str,
        focused: list[str],
        previous_verify: VerifyOutput | None,
        session_id: str | None,
        guide_sha_holder: dict[str, str],
    ) -> tuple[str, HarnessResult]:
        cfg = self.deps.config
        guide = load_guide(
            AgentRole.BUILDER, override_dir=cfg.prompt_override_dir(AgentRole.BUILDER)
        )
        bundle = build_builder_prompt(
            guide,
            spec_text=spec_text,
            writes=self.role.writes,
            protected_paths=cfg.protected_paths,
            commands=cfg.quality,
            focused_tests=focused,
            previous_verify=previous_verify,
            reviewer_findings=self.reviewer_findings or None,
        )
        guide_sha_holder["sha"] = bundle.guide_sha
        harness = self.deps.harness
        phase_id = self._start_phase(
            PhaseName.REVISION if self.reviewer_findings else PhaseName.BUILD,
            PhaseKind.AGENT,
            "builder",
            attempt=attempt,
            timeout_s=self.role.timeout_s,
            phase_cap_usd=self.role.phase_cap_usd,
            harness=harness.name,
            harness_version=harness.version(),
            model_id=self.role.model,
            session_id=session_id,
        )
        issued = await self.deps.keys.issue(
            settings=self.deps.settings,
            task_id=self.task_id,
            phase_id=phase_id,
            role=AgentRole.BUILDER,
            harness=harness.name,
            model=self.role.model,
            cap_usd=self.role.phase_cap_usd,
            timeout_s=self.role.timeout_s,
        )
        if issued.key_id is not None:
            self.writer.set_phase_key(phase_id, issued.key_id)
        request = HarnessRequest(
            role=AgentRole.BUILDER,
            prompt=bundle.user_prompt,
            system_prompt=bundle.system_prompt,
            output_schema=BuildOutput.json_schema(),
            cwd=self.worktree.path,
            env=agent_env_with_hooks(issued.env, self.deps.config.protected_paths),
            model=self.role.model,
            timeout_s=self.role.timeout_s,
            tools=self.role.tools,
            thinking=self.role.thinking,
            max_turns=self.role.max_turns,
            budget_usd=self.role.phase_cap_usd,
            resume_session_id=session_id,
            hooks_dir=self.deps.hooks_dir,
            hook_python=self.deps.hook_python,
        )
        process_id = self.writer.start_process(
            run_id=self.run_id, phase_id=phase_id, kind=ProcessKind.AGENT, name=harness.name
        )
        self._event(
            EventKind.AGENT_START,
            {
                "role": "builder",
                "harness": harness.name,
                "harness_version": harness.version(),
                "gen_ai.request.model": self.role.model,
                "guide_sha": bundle.guide_sha,
                "virtual_key_id": issued.key_id,
                "sections": list(bundle.sections),
                "attempt": attempt,
                "resume": session_id is not None,
            },
            phase_id=phase_id,
            actor="builder",
        )
        sink = trace_tool_call_sink(
            self.writer,
            task_id=self.task_id,
            run_id=self.run_id,
            phase_id=phase_id,
            actor="builder",
            process_id=process_id,
        )
        self.builder_calls += 1
        denials = 0

        def counting(call: ToolCall) -> None:
            nonlocal denials
            if call.denied:
                denials += 1
            sink(call)

        result = await run_guarded(
            harness,
            request,
            on_tool_call=counting,
            on_process=lambda pid: self.writer.set_process_pid(process_id, pid),
            guard=self.deps.keys.guard(
                issued,
                writer=self.writer,
                task_id=self.task_id,
                run_id=self.run_id,
                phase_id=phase_id,
                phase_cap_usd=self.role.phase_cap_usd,
            ),
        )
        result = await self.deps.keys.settle(issued, result)
        self.writer.end_process(process_id, exit_code=result.exit_code)
        self.cost_usd += result.usage.cost_usd
        self._event(
            EventKind.AGENT_END,
            {
                "outcome": result.outcome,
                "session_id": result.session_id,
                "gen_ai.usage.input_tokens": result.usage.input_tokens,
                "gen_ai.usage.output_tokens": result.usage.output_tokens,
                "cache_read_tokens": result.usage.cache_read_tokens,
                "cache_write_tokens": result.usage.cache_write_tokens,
                "cost_usd": result.usage.cost_usd,
                "num_turns": result.num_turns,
                "retries": result.retries,
                "hook_denials": denials,
                "error": result.error,
            },
            phase_id=phase_id,
            actor="builder",
        )
        status = PhaseStatus.PASS if result.completed else PhaseStatus.FAIL
        self._finish_phase(phase_id, status, error=result.error, result=result)
        return phase_id, result

    # --- quality --------------------------------------------------------------------

    async def _quality(self, phase_id: str, attempt: int, focused: list[str]) -> GateResult | None:
        ctx = GateContext(
            task_id=self.task_id, run_id=self.run_id, phase_id=phase_id, attempt=attempt
        )
        outcome = await run_quality(
            self.worktree.path,
            self.deps.config.quality,
            env=self.deps.settings.subprocess_base_env(),
            focused_tests=focused,
            runner=self.deps.runner,
        )
        for result in outcome.results:
            write_gate(self.writer, result, ctx)
        return outcome.first_failure

    # --- the loop -----------------------------------------------------------------

    async def run(self) -> BuildOutcome:
        spec_path = self.worktree.path.joinpath(*self.spec_rel.split("/"))
        spec_text = spec_path.read_text(encoding="utf-8")
        focused = parse_focused_tests(spec_text)
        snapshot = take_snapshot(self.worktree.path)
        session_id: str | None = None
        previous_verify: VerifyOutput | None = self.initial_verify
        envelope_reprompts = 0
        infra_retries = 0
        guide_sha: dict[str, str] = {}
        attempt = 0
        while True:
            attempt += 1
            decision = check_budget(
                self.writer, task_id=self.task_id, phase_cap_usd=self.role.phase_cap_usd
            )
            if not decision.ok:
                self._event(EventKind.BUDGET_STOP, decision.payload(), phase_id=None)
                return self._blocked(
                    BlockedReason.BUDGET, decision.reason or "budget", phase_id=None
                )
            build_phase_id, result = await self._call_builder(
                attempt=attempt,
                spec_text=spec_text,
                focused=focused,
                previous_verify=previous_verify,
                session_id=session_id,
                guide_sha_holder=guide_sha,
            )
            session_id = result.session_id or session_id

            if (result.error or "").startswith("error_max_budget_usd"):
                return self._blocked(
                    BlockedReason.BUDGET, result.error or "budget", phase_id=build_phase_id
                )
            if result.outcome in ("timeout", "killed"):
                return self._blocked(
                    BlockedReason.TIMEOUT, result.error or result.outcome, phase_id=build_phase_id
                )
            if result.outcome == "error":
                error = result.error or "harness error"
                if error.startswith("error_max_budget_usd"):
                    return self._blocked(BlockedReason.BUDGET, error, phase_id=build_phase_id)
                if error.startswith("error_max_turns"):
                    return self._blocked(BlockedReason.REPAIR_CAP, error, phase_id=build_phase_id)
                if not error.startswith("error_max_structured_output_retries"):
                    if infra_retries < constants.INFRA_RETRIES:
                        delay = constants.INFRA_BACKOFF_S[infra_retries]
                        infra_retries += 1
                        self._event(
                            EventKind.ERROR,
                            {"infra_retry": infra_retries, "delay_s": delay, "error": error},
                            phase_id=build_phase_id,
                        )
                        await self.deps.sleep(delay)
                        attempt -= 1  # an infra retry is not a new attempt
                        continue
                    return self._blocked(
                        BlockedReason.TIMEOUT,
                        f"harness failed {constants.INFRA_RETRIES} times: {error}",
                        phase_id=build_phase_id,
                    )

            gates_phase_id = self._start_phase(
                PhaseName.CODE_GATES,
                PhaseKind.CODE,
                "orchestrator",
                attempt=attempt,
                timeout_s=3600,
            )
            ctx = GateContext(
                task_id=self.task_id, run_id=self.run_id, phase_id=gates_phase_id, attempt=attempt
            )
            schema_result, envelope = envelope_schema_gate(result.envelope_text, BuildOutput)
            record_envelope(
                self.writer,
                run_id=self.run_id,
                phase_id=build_phase_id,
                agent=AgentRole.BUILDER,
                output_type=OutputType.BUILD,
                text=result.envelope_text,
                result=schema_result,
                attempt=attempt,
            )
            write_gate(self.writer, schema_result, ctx)
            if envelope is None:
                self._finish_phase(gates_phase_id, PhaseStatus.FAIL, error="envelope_schema")
                if envelope_reprompts < constants.ENVELOPE_REPROMPT_CAP:
                    envelope_reprompts += 1
                    previous_verify = verify_from(schema_result)
                    continue
                return self._blocked(
                    BlockedReason.REPAIR_CAP,
                    schema_result.correction_text(),
                    phase_id=gates_phase_id,
                )

            perm = permissions_gate(
                self.worktree.path,
                snapshot,
                writes=self.role.writes,
                protected_paths=self.deps.config.protected_paths,
            )
            record_permissions(self.writer, perm, ctx)
            if not perm.result.passed:
                self._finish_phase(gates_phase_id, PhaseStatus.FAIL, error="permissions_enforce")
                reason = perm.blocked_reason or BlockedReason.PROTECTED_PATH
                return self._blocked(reason, perm.result.correction_text(), phase_id=gates_phase_id)

            claimed = [*envelope.changed_files, *envelope.tests_added]
            claims = claim_gates(self.worktree.path, perm.changes, claimed)
            for gate_result in claims.results:
                write_gate(self.writer, gate_result, ctx)
            failing = claims.first_failure
            if failing is None:
                failing = await self._quality(gates_phase_id, attempt, focused)
            if failing is None:
                self._finish_phase(gates_phase_id, PhaseStatus.PASS)
                return await self._commit(envelope)

            self._finish_phase(gates_phase_id, PhaseStatus.FAIL, error=str(failing.gate))
            if self.repairs < constants.REPAIR_CAP:
                self.repairs += 1
                previous_verify = verify_from(failing)
                continue
            return self._blocked(
                BlockedReason.REPAIR_CAP, failing.correction_text(), phase_id=gates_phase_id
            )

    async def _commit(self, envelope: BuildOutput) -> BuildOutcome:
        phase_id = self._start_phase(
            PhaseName.COMMIT, PhaseKind.CODE, "orchestrator", attempt=1, timeout_s=600
        )
        message = envelope.commit_message.strip() or f"factory: build for {self.task_id}"
        sha = commit_all(self.worktree.path, message, settings=self.deps.settings)
        if sha is None and self.reviewer_findings:
            # a revision may argue a finding away without touching the tree
            sha = run_git(["rev-parse", "HEAD"], cwd=self.worktree.path).stdout.strip()
            self._event(
                EventKind.HANDOFF,
                {"commit": sha, "message": message, "unchanged": True},
                phase_id=phase_id,
            )
            self._finish_phase(phase_id, PhaseStatus.PASS)
            return BuildOutcome(
                "pass", self.run_id, sha, None, "", self.repairs, self.builder_calls, self.cost_usd
            )
        if sha is None:
            self._finish_phase(phase_id, PhaseStatus.FAIL, error="nothing to commit")
            return self._blocked(
                BlockedReason.REPAIR_CAP,
                "the builder reported success but changed nothing",
                phase_id=phase_id,
            )
        self._event(EventKind.HANDOFF, {"commit": sha, "message": message}, phase_id=phase_id)
        self._finish_phase(phase_id, PhaseStatus.PASS)
        return BuildOutcome(
            "pass", self.run_id, sha, None, "", self.repairs, self.builder_calls, self.cost_usd
        )
