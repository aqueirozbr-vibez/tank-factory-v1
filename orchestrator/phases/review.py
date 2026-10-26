"""The review phase: a fresh read-only session on the other vendor (PRD §Capability 4).

Inputs are the spec, the diff, the builder's envelope and the gate
evidence; never the builder's transcript. Code checks the envelope's
consistency and forces ``revision_needed`` when an acceptance criterion
received no verdict. Scoring and the revision loop are unit 28.
"""

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from orchestrator import constants
from orchestrator.budget import check_budget
from orchestrator.budget.watch import run_guarded
from orchestrator.errors import ConfigError, PromptError
from orchestrator.gates.base import GateContext, write_gate
from orchestrator.gates.envelope_schema import envelope_schema_gate, record_envelope
from orchestrator.gates.spec import parse_spec
from orchestrator.gates.verdict import verdict_consistent_gate
from orchestrator.harness.base import HarnessRequest, HarnessResult
from orchestrator.harness.sink import trace_tool_call_sink
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
from orchestrator.models.envelopes import ReviewOutput, Verdict
from orchestrator.phases.build import BuildDeps
from orchestrator.phases.recorder import PhaseRecorder
from orchestrator.prompt_builder import PromptBundle, build_reviewer_prompt, load_guide
from orchestrator.workspace.worktree import Worktree, diff_against_base


@dataclass(frozen=True)
class ReviewOutcome:
    status: Literal["reviewed", "blocked"]
    run_id: str
    envelope: ReviewOutput | None
    verdict: Verdict | None
    missing_ac: tuple[str, ...]
    blocked_reason: BlockedReason | None
    evidence: str
    reviewer_calls: int
    reprompts: int
    cost_usd: float
    session_id: str | None
    phase_id: str | None

    @property
    def reviewed(self) -> bool:
        return self.status == "reviewed"


def ac_ids_of(spec_text: str) -> tuple[str, ...]:
    return tuple(f"AC{number}" for number, _ in parse_spec(spec_text).criteria)


def render_gate_evidence(rows: Sequence[sqlite3.Row]) -> str:
    """The run's gate rows as lines the reviewer can cite."""
    if not rows:
        return "No gate results recorded for this run."
    lines: list[str] = []
    for row in rows:
        state = "pass" if int(row["passed"]) else "fail"
        line = f"- {row['gate']} (attempt {row['attempt']}): {state}"
        raw = row["violations_json"]
        violations = json.loads(raw) if raw else []
        if violations:
            shown = "; ".join(f"{v.get('item')}: {v.get('problem')}" for v in violations[:3])
            line += f"; {shown}"
        if row["command"]:
            line += f" [{row['command']}; exit code {row['exit_code']}]"
        lines.append(line)
    return "\n".join(lines)


class ReviewPhase:
    def __init__(
        self,
        deps: BuildDeps,
        *,
        task_id: str,
        worktree: Worktree,
        spec_rel: str,
        run_id: str,
        attempt: int = 1,
        seq_start: int | None = None,
    ) -> None:
        roster = deps.config.roster
        if roster.builder.vendor_name == roster.reviewer.vendor_name:
            raise ConfigError(
                f"builder and reviewer share one vendor ({roster.builder.vendor_name});"
                " the review phase refuses to start (PRD Capability 4)"
            )
        self.deps = deps
        self.writer = deps.writer
        self.task_id = task_id
        self.worktree = worktree
        self.spec_rel = spec_rel
        self.run_id = run_id
        self.attempt = attempt
        self.role = roster.reviewer
        start = self.writer.last_phase_seq(run_id) if seq_start is None else seq_start
        self.recorder = PhaseRecorder(self.writer, task_id=task_id, run_id=run_id, seq_start=start)
        self.reviewer_calls = 0
        self.reprompts = 0
        self.cost_usd = 0.0
        self.session_id: str | None = None
        self.phase_id: str | None = None

    @property
    def next_seq(self) -> int:
        return self.recorder.next_seq

    # --- inputs ----------------------------------------------------------------------

    def _spec_text(self) -> str:
        path = self.worktree.path.joinpath(*self.spec_rel.split("/"))
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    def _inputs(self) -> dict[str, str]:
        builder_envelope = self.writer.latest_envelope(self.run_id, OutputType.BUILD)
        return {
            "policy_text": self.deps.config.review_policy_yaml,
            "spec_text": self._spec_text(),
            "builder_envelope_text": builder_envelope or "(no valid builder envelope recorded)",
            "gate_evidence": render_gate_evidence(self.writer.gate_results_for_run(self.run_id)),
            "diff": diff_against_base(self.worktree.path, self.worktree.base_sha),
        }

    # --- bookkeeping ------------------------------------------------------------------

    def _blocked(
        self, reason: BlockedReason, evidence: str, *, phase_id: str | None
    ) -> ReviewOutcome:
        self.writer.set_task_status(self.task_id, TaskStatus.BLOCKED, blocked_reason=reason)
        self.recorder.event(
            EventKind.ERROR,
            {"blocked_reason": str(reason), "evidence": evidence[-constants.OUTPUT_TAIL_CHARS :]},
            phase_id=phase_id,
        )
        return ReviewOutcome(
            "blocked",
            self.run_id,
            None,
            None,
            (),
            reason,
            evidence,
            self.reviewer_calls,
            self.reprompts,
            self.cost_usd,
            self.session_id,
            phase_id,
        )

    async def _call_reviewer(
        self, bundle: PromptBundle, *, session_id: str | None
    ) -> tuple[str, HarnessResult]:
        harness = self.deps.harness
        phase_id = self.recorder.start(
            PhaseName.REVIEW,
            PhaseKind.AGENT,
            "reviewer",
            attempt=self.attempt,
            timeout_s=self.role.timeout_s,
            phase_cap_usd=self.role.phase_cap_usd,
            harness=harness.name,
            harness_version=harness.version(),
            model_id=self.role.model,
            session_id=session_id,
        )
        self.phase_id = phase_id
        issued = await self.deps.keys.issue(
            settings=self.deps.settings,
            task_id=self.task_id,
            phase_id=phase_id,
            role=AgentRole.REVIEWER,
            harness=harness.name,
            model=self.role.model,
            cap_usd=self.role.phase_cap_usd,
            timeout_s=self.role.timeout_s,
        )
        if issued.key_id is not None:
            self.writer.set_phase_key(phase_id, issued.key_id)
        request = HarnessRequest(
            role=AgentRole.REVIEWER,
            prompt=bundle.user_prompt,
            system_prompt=bundle.system_prompt,
            output_schema=ReviewOutput.json_schema(),
            cwd=self.worktree.path,
            env=issued.env,
            model=self.role.model,
            timeout_s=self.role.timeout_s,
            tools=self.role.tools,
            thinking=self.role.thinking,
            max_turns=self.role.max_turns,
            budget_usd=self.role.phase_cap_usd,
            resume_session_id=session_id,
        )
        process_id = self.writer.start_process(
            run_id=self.run_id, phase_id=phase_id, kind=ProcessKind.AGENT, name=harness.name
        )
        self.recorder.event(
            EventKind.AGENT_START,
            {
                "role": "reviewer",
                "harness": harness.name,
                "harness_version": harness.version(),
                "gen_ai.request.model": self.role.model,
                "guide_sha": bundle.guide_sha,
                "virtual_key_id": issued.key_id,
                "sections": list(bundle.sections),
                "attempt": self.attempt,
                "resume": session_id is not None,
                "tools": list(self.role.tools),
            },
            phase_id=phase_id,
            actor="reviewer",
        )
        sink = trace_tool_call_sink(
            self.writer,
            task_id=self.task_id,
            run_id=self.run_id,
            phase_id=phase_id,
            actor="reviewer",
            process_id=process_id,
        )
        self.reviewer_calls += 1
        result = await run_guarded(
            harness,
            request,
            on_tool_call=sink,
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
        self.recorder.event(
            EventKind.AGENT_END,
            {
                "outcome": result.outcome,
                "session_id": result.session_id,
                "gen_ai.usage.input_tokens": result.usage.input_tokens,
                "gen_ai.usage.output_tokens": result.usage.output_tokens,
                "cost_usd": result.usage.cost_usd,
                "num_turns": result.num_turns,
                "retries": result.retries,
                "error": result.error,
            },
            phase_id=phase_id,
            actor="reviewer",
        )
        self.recorder.finish(
            phase_id,
            PhaseStatus.PASS if result.completed else PhaseStatus.FAIL,
            error=result.error,
            result=result,
        )
        return phase_id, result

    # --- the loop -----------------------------------------------------------------------

    async def run(self) -> ReviewOutcome:
        decision = check_budget(
            self.writer, task_id=self.task_id, phase_cap_usd=self.role.phase_cap_usd
        )
        if not decision.ok:
            self.recorder.event(EventKind.BUDGET_STOP, decision.payload(), phase_id=None)
            return self._blocked(BlockedReason.BUDGET, decision.reason or "budget", phase_id=None)
        inputs = self._inputs()
        ac_ids = ac_ids_of(inputs["spec_text"])
        cfg = self.deps.config
        guide = load_guide(
            AgentRole.REVIEWER, override_dir=cfg.prompt_override_dir(AgentRole.REVIEWER)
        )
        session_id: str | None = None
        correction: str | None = None
        envelope_reprompts = 0
        verdict_reprompts = 0
        infra_retries = 0
        call = 0
        while True:
            call += 1
            try:
                bundle = build_reviewer_prompt(guide, **inputs, correction=correction)
            except PromptError as exc:
                return self._blocked(
                    BlockedReason.HUMAN_DECISION_NEEDED, f"prompt refused: {exc}", phase_id=None
                )
            phase_id, result = await self._call_reviewer(bundle, session_id=session_id)
            session_id = result.session_id or session_id
            self.session_id = session_id

            if (result.error or "").startswith("error_max_budget_usd"):
                return self._blocked(
                    BlockedReason.BUDGET, result.error or "budget", phase_id=phase_id
                )
            if result.outcome in ("timeout", "killed"):
                return self._blocked(
                    BlockedReason.TIMEOUT, result.error or result.outcome, phase_id=phase_id
                )
            if result.outcome == "error":
                error = result.error or "harness error"
                if error.startswith("error_max_budget_usd"):
                    return self._blocked(BlockedReason.BUDGET, error, phase_id=phase_id)
                if error.startswith("error_max_turns"):
                    return self._blocked(
                        BlockedReason.HUMAN_DECISION_NEEDED, error, phase_id=phase_id
                    )
                if not error.startswith("error_max_structured_output_retries"):
                    if infra_retries < constants.INFRA_RETRIES:
                        delay = constants.INFRA_BACKOFF_S[infra_retries]
                        infra_retries += 1
                        self.recorder.event(
                            EventKind.ERROR,
                            {"infra_retry": infra_retries, "delay_s": delay, "error": error},
                            phase_id=phase_id,
                        )
                        await self.deps.sleep(delay)
                        continue
                    return self._blocked(
                        BlockedReason.TIMEOUT,
                        f"harness failed {constants.INFRA_RETRIES} times: {error}",
                        phase_id=phase_id,
                    )

            ctx = GateContext(
                task_id=self.task_id, run_id=self.run_id, phase_id=phase_id, attempt=call
            )
            schema_result, envelope = envelope_schema_gate(result.envelope_text, ReviewOutput)
            record_envelope(
                self.writer,
                run_id=self.run_id,
                phase_id=phase_id,
                agent=AgentRole.REVIEWER,
                output_type=OutputType.REVIEW,
                text=result.envelope_text,
                result=schema_result,
                attempt=call,
            )
            write_gate(self.writer, schema_result, ctx)
            if envelope is None:
                if envelope_reprompts < constants.ENVELOPE_REPROMPT_CAP:
                    envelope_reprompts += 1
                    self.reprompts += 1
                    correction = schema_result.correction_text()
                    continue
                return self._blocked(
                    BlockedReason.HUMAN_DECISION_NEEDED,
                    schema_result.correction_text(),
                    phase_id=phase_id,
                )

            check = verdict_consistent_gate(envelope, ac_ids=ac_ids)
            write_gate(self.writer, check.result, ctx)
            if not check.result.passed:
                if verdict_reprompts < constants.VERDICT_REPROMPT_CAP:
                    verdict_reprompts += 1
                    self.reprompts += 1
                    correction = check.result.correction_text()
                    continue
                if check.hard:
                    return self._blocked(
                        BlockedReason.HUMAN_DECISION_NEEDED,
                        check.result.correction_text(),
                        phase_id=phase_id,
                    )
            verdict: Verdict = envelope.verdict
            if check.missing_ac and verdict == "accepted":
                verdict = "revision_needed"
                self.recorder.event(
                    EventKind.LOG,
                    {"verdict_forced": verdict, "missing_ac": list(check.missing_ac)},
                    phase_id=phase_id,
                )
            self.recorder.event(
                EventKind.HANDOFF,
                {
                    "verdict": verdict,
                    "reviewer_verdict": envelope.verdict,
                    "findings": len(envelope.findings),
                    "blocking": list(envelope.blocking),
                    "missing_ac": list(check.missing_ac),
                },
                phase_id=phase_id,
            )
            return ReviewOutcome(
                "reviewed",
                self.run_id,
                envelope,
                verdict,
                check.missing_ac,
                None,
                "",
                self.reviewer_calls,
                self.reprompts,
                self.cost_usd,
                session_id,
                phase_id,
            )
