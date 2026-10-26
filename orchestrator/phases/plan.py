"""The plan phase: one planner, one spec, every claim and the spec checked (PRD §Cap. 2)."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from orchestrator import constants
from orchestrator.budget import check_budget
from orchestrator.budget.watch import run_guarded
from orchestrator.errors import PromptError
from orchestrator.gates.base import GateContext, GateResult, write_gate
from orchestrator.gates.claims import claim_gates
from orchestrator.gates.envelope_schema import envelope_schema_gate, record_envelope
from orchestrator.gates.permissions import permissions_gate, record_permissions, take_snapshot
from orchestrator.gates.spec import ParsedSpec, run_spec_gates
from orchestrator.harness.base import HarnessRequest, HarnessResult
from orchestrator.harness.sink import trace_tool_call_sink
from orchestrator.instruments.risk import (
    RiskBreakdown,
    RiskInstrument,
    classify,
    scope_paths,
    write_risk_header,
)
from orchestrator.intake import messages
from orchestrator.intake.issue_fetcher import TaggedIssue
from orchestrator.intake.ports import GitHubPort
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
from orchestrator.models.envelopes import PlanOutput
from orchestrator.phases.build import BuildDeps
from orchestrator.prompt_builder import build_planner_prompt, load_guide, load_prompt_text
from orchestrator.workspace.hooks import agent_env_with_hooks
from orchestrator.workspace.worktree import Worktree, blob_sha, commit_all

PLANNER_WRITES = ("specs/",)
GUIDANCE_FILES = ("CLAUDE.md", "AGENTS.md")
GUIDANCE_CAP = 20_000
SLUG = re.compile(r"[^a-z0-9]+")


class HoldoutPort(Protocol):
    @property
    def tests_dir(self) -> Path: ...

    def refresh(self) -> str: ...

    def has_test(self, test_id: str) -> bool: ...

    def copy_into(self, workspace: Path) -> Path: ...


def slugify(title: str, *, limit: int = 40) -> str:
    slug = SLUG.sub("-", title.lower()).strip("-")
    return (slug[:limit].rstrip("-")) or "task"


def spec_path_for(issue: int, title: str | None) -> str:
    return f"specs/{issue}-{slugify(title or 'task')}.md"


def repository_guidance(worktree: Path) -> str | None:
    for name in GUIDANCE_FILES:
        candidate = worktree / name
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8", errors="replace")[:GUIDANCE_CAP]
    return None


def adr_index(worktree: Path) -> list[str]:
    adr_dir = worktree / "docs" / "adr"
    if not adr_dir.is_dir():
        return []
    entries: list[str] = []
    for file in sorted(adr_dir.glob("*.md")):
        heading = ""
        for line in file.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("#"):
                heading = line.lstrip("#").strip()
                break
        entries.append(f"docs/adr/{file.name}: {heading}" if heading else f"docs/adr/{file.name}")
    return entries


@dataclass(frozen=True)
class PlanOutcome:
    status: Literal["spec_written", "blocked"]
    run_id: str
    spec_rel: str
    envelope: PlanOutput | None
    spec_text: str | None
    spec: ParsedSpec | None
    blocked_reason: BlockedReason | None
    evidence: str
    planner_calls: int
    corrections: int
    cost_usd: float
    risk: RiskBreakdown | None = None
    spec_sha: str | None = None

    @property
    def written(self) -> bool:
        return self.status == "spec_written"


class PlanPhase:
    def __init__(
        self,
        deps: BuildDeps,
        *,
        task_id: str,
        worktree: Worktree,
        issue: TaggedIssue,
        run_id: str,
        holdout: HoldoutPort,
        github: GitHubPort | None = None,
        instrument: RiskInstrument | None = None,
        seq_start: int = 0,
    ) -> None:
        self.deps = deps
        self.writer = deps.writer
        self.task_id = task_id
        self.worktree = worktree
        self.issue = issue
        self.run_id = run_id
        self.holdout = holdout
        self.github = github
        self.instrument = instrument
        self.role = deps.config.roster.planner
        self._seq = seq_start
        self.cost_usd = 0.0
        self.planner_calls = 0
        self.corrections = 0
        self.spec_rel = spec_path_for(issue.number, issue.title)

    @property
    def next_seq(self) -> int:
        return self._seq

    # --- helpers (mirror BuildPhase; a shared base is a later refactor) --------------

    def _event(
        self,
        kind: EventKind,
        payload: object,
        *,
        phase_id: str | None,
        actor: str = "orchestrator",
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
        self._seq += 1
        phase_id = self.writer.start_phase(
            run_id=self.run_id,
            seq=self._seq,
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
    ) -> PlanOutcome:
        self.writer.set_task_status(self.task_id, TaskStatus.BLOCKED, blocked_reason=reason)
        self._event(
            EventKind.ERROR,
            {"blocked_reason": str(reason), "evidence": evidence[-constants.OUTPUT_TAIL_CHARS :]},
            phase_id=phase_id,
        )
        return PlanOutcome(
            "blocked",
            self.run_id,
            self.spec_rel,
            None,
            self._spec_text(),
            None,
            reason,
            evidence,
            self.planner_calls,
            self.corrections,
            self.cost_usd,
        )

    async def _needs_human(self, body: str) -> None:
        """Post the reason and apply factory:needs-human; code under the App identity."""
        if self.github is None:
            return
        await self.github.create_comment(self.issue.number, body, task_id=self.task_id)
        await self.github.add_labels(
            self.issue.number, [constants.LABEL_NEEDS_HUMAN], task_id=self.task_id
        )

    def _spec_text(self) -> str | None:
        path = self.worktree.path.joinpath(*self.spec_rel.split("/"))
        return path.read_text(encoding="utf-8") if path.is_file() else None

    # --- the planner call ---------------------------------------------------------

    async def _call_planner(
        self, *, attempt: int, session_id: str | None, correction: str | None
    ) -> tuple[str, HarnessResult]:
        cfg = self.deps.config
        override = cfg.prompt_override_dir(AgentRole.PLANNER)
        guide = load_guide(AgentRole.PLANNER, override_dir=override)
        template = load_prompt_text(AgentRole.PLANNER, "spec-template.md", override_dir=override)
        bundle = build_planner_prompt(
            guide,
            spec_path=self.spec_rel,
            spec_template=template,
            issue_block=self.issue.render_for_agent(),
            agents_md=repository_guidance(self.worktree.path),
            adr_index=adr_index(self.worktree.path),
            correction=correction,
        )
        harness = self.deps.harness
        phase_id = self._start_phase(
            PhaseName.PLAN,
            PhaseKind.AGENT,
            "planner",
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
            role=AgentRole.PLANNER,
            harness=harness.name,
            model=self.role.model,
            cap_usd=self.role.phase_cap_usd,
            timeout_s=self.role.timeout_s,
        )
        if issued.key_id is not None:
            self.writer.set_phase_key(phase_id, issued.key_id)
        request = HarnessRequest(
            role=AgentRole.PLANNER,
            prompt=bundle.user_prompt,
            system_prompt=bundle.system_prompt,
            output_schema=PlanOutput.json_schema(),
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
                "role": "planner",
                "harness": harness.name,
                "harness_version": harness.version(),
                "gen_ai.request.model": self.role.model,
                "guide_sha": bundle.guide_sha,
                "virtual_key_id": issued.key_id,
                "sections": list(bundle.sections),
                "attempt": attempt,
                "resume": session_id is not None,
                "spec_path": self.spec_rel,
            },
            phase_id=phase_id,
            actor="planner",
        )
        sink = trace_tool_call_sink(
            self.writer,
            task_id=self.task_id,
            run_id=self.run_id,
            phase_id=phase_id,
            actor="planner",
            process_id=process_id,
        )
        self.planner_calls += 1
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
        self._event(
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
            actor="planner",
        )
        self._finish_phase(
            phase_id,
            PhaseStatus.PASS if result.completed else PhaseStatus.FAIL,
            error=result.error,
            result=result,
        )
        return phase_id, result

    # --- the loop -------------------------------------------------------------------

    async def run(self) -> PlanOutcome:
        decision = check_budget(
            self.writer, task_id=self.task_id, phase_cap_usd=self.role.phase_cap_usd
        )
        if not decision.ok:
            self._event(EventKind.BUDGET_STOP, decision.payload(), phase_id=None)
            return self._blocked(BlockedReason.BUDGET, decision.reason or "budget", phase_id=None)
        snapshot = take_snapshot(self.worktree.path)
        session_id: str | None = None
        correction: str | None = None
        envelope_reprompts = 0
        infra_retries = 0
        attempt = 0
        while True:
            attempt += 1
            try:
                plan_phase_id, result = await self._call_planner(
                    attempt=attempt, session_id=session_id, correction=correction
                )
            except PromptError as exc:
                return self._blocked(
                    BlockedReason.SPEC_GATE, f"prompt refused: {exc}", phase_id=None
                )
            session_id = result.session_id or session_id

            if (result.error or "").startswith("error_max_budget_usd"):
                return self._blocked(
                    BlockedReason.BUDGET, result.error or "budget", phase_id=plan_phase_id
                )
            if result.outcome in ("timeout", "killed"):
                return self._blocked(
                    BlockedReason.TIMEOUT, result.error or result.outcome, phase_id=plan_phase_id
                )
            if result.outcome == "error":
                error = result.error or "harness error"
                if error.startswith("error_max_budget_usd"):
                    return self._blocked(BlockedReason.BUDGET, error, phase_id=plan_phase_id)
                if error.startswith("error_max_turns"):
                    return self._blocked(BlockedReason.SPEC_GATE, error, phase_id=plan_phase_id)
                if not error.startswith("error_max_structured_output_retries"):
                    if infra_retries < constants.INFRA_RETRIES:
                        delay = constants.INFRA_BACKOFF_S[infra_retries]
                        infra_retries += 1
                        self._event(
                            EventKind.ERROR,
                            {"infra_retry": infra_retries, "delay_s": delay, "error": error},
                            phase_id=plan_phase_id,
                        )
                        await self.deps.sleep(delay)
                        attempt -= 1
                        continue
                    return self._blocked(
                        BlockedReason.TIMEOUT,
                        f"harness failed {constants.INFRA_RETRIES} times: {error}",
                        phase_id=plan_phase_id,
                    )

            gates_phase_id = self._start_phase(
                PhaseName.SPEC_GATE, PhaseKind.CODE, "orchestrator", attempt=attempt, timeout_s=600
            )
            ctx = GateContext(
                task_id=self.task_id, run_id=self.run_id, phase_id=gates_phase_id, attempt=attempt
            )
            schema_result, envelope = envelope_schema_gate(result.envelope_text, PlanOutput)
            record_envelope(
                self.writer,
                run_id=self.run_id,
                phase_id=plan_phase_id,
                agent=AgentRole.PLANNER,
                output_type=OutputType.PLAN,
                text=result.envelope_text,
                result=schema_result,
                attempt=attempt,
            )
            write_gate(self.writer, schema_result, ctx)
            if envelope is None:
                self._finish_phase(gates_phase_id, PhaseStatus.FAIL, error="envelope_schema")
                if envelope_reprompts < constants.ENVELOPE_REPROMPT_CAP:
                    envelope_reprompts += 1
                    correction = schema_result.correction_text()
                    continue
                return self._blocked(
                    BlockedReason.SPEC_GATE,
                    schema_result.correction_text(),
                    phase_id=gates_phase_id,
                )

            perm = permissions_gate(
                self.worktree.path,
                snapshot,
                writes=PLANNER_WRITES,
                protected_paths=self.deps.config.protected_paths,
            )
            record_permissions(self.writer, perm, ctx)
            if not perm.result.passed:
                self._finish_phase(gates_phase_id, PhaseStatus.FAIL, error="permissions_enforce")
                return self._blocked(
                    BlockedReason.PROTECTED_PATH,
                    perm.result.correction_text(),
                    phase_id=gates_phase_id,
                )

            claimed = [*envelope.artifacts, self.spec_rel]
            changed_paths = {c.path for c in perm.changes}
            spec_file = self.worktree.path.joinpath(*self.spec_rel.split("/"))
            if self.spec_rel not in changed_paths and spec_file.is_file():
                # a re-plan may rewrite the same spec byte for byte: no lie to catch
                self._event(
                    EventKind.LOG,
                    {"replan": "spec unchanged", "spec": self.spec_rel},
                    phase_id=gates_phase_id,
                )
                claimed = [p for p in claimed if p != self.spec_rel]
            claims = claim_gates(self.worktree.path, perm.changes, claimed)
            for gate_result in claims.results:
                write_gate(self.writer, gate_result, ctx)
            failing: GateResult | None = claims.first_failure

            if failing is None:
                spec_text = self._spec_text() or ""
                self.holdout.refresh()
                gates = run_spec_gates(
                    spec_text, has_test=self.holdout.has_test, worktree=self.worktree.path
                )
                for gate_result in gates.results:
                    write_gate(self.writer, gate_result, ctx)
                if gates.missing_holdout:
                    self._finish_phase(gates_phase_id, PhaseStatus.FAIL, error="missing_holdout")
                    await self._needs_human(messages.missing_holdout(gates.missing_holdout))
                    return self._blocked(
                        BlockedReason.MISSING_HOLDOUT,
                        "holdout tests missing: " + ", ".join(gates.missing_holdout),
                        phase_id=gates_phase_id,
                    )
                failing = gates.first_failure
                if failing is None:
                    questions = list(
                        dict.fromkeys([*gates.open_questions, *envelope.open_questions])
                    )
                    self.writer.set_task_spec(self.task_id, spec_path=self.spec_rel, spec_sha="")
                    if questions:
                        self._finish_phase(gates_phase_id, PhaseStatus.FAIL, error="open_questions")
                        await self._needs_human(
                            messages.open_questions(questions, self.issue.language)
                        )
                        return self._blocked(
                            BlockedReason.OPEN_QUESTIONS,
                            "open questions:\n" + "\n".join(f"- {q}" for q in questions),
                            phase_id=gates_phase_id,
                        )
                    risk: RiskBreakdown | None = None
                    if self.instrument is not None:
                        risk = classify(
                            self.instrument,
                            scope=scope_paths(gates.spec),
                            inputs=envelope.risk_inputs,
                            worktree=self.worktree.path,
                        )
                        self._event(
                            EventKind.LOG, {"risk": risk.payload()}, phase_id=gates_phase_id
                        )
                        if risk.unclassifiable:
                            self._finish_phase(
                                gates_phase_id, PhaseStatus.FAIL, error="risk_unclassifiable"
                            )
                            if self.corrections < constants.SPEC_CORRECTION_CAP:
                                self.corrections += 1
                                correction = risk.correction_text()
                                continue
                            return self._blocked(
                                BlockedReason.SPEC_GATE,
                                risk.correction_text(),
                                phase_id=gates_phase_id,
                            )
                    self._finish_phase(gates_phase_id, PhaseStatus.PASS)
                    spec_sha = await self._finalize_spec(risk, attempt=attempt)
                    return PlanOutcome(
                        "spec_written",
                        self.run_id,
                        self.spec_rel,
                        envelope,
                        self._spec_text(),
                        gates.spec,
                        None,
                        "",
                        self.planner_calls,
                        self.corrections,
                        self.cost_usd,
                        risk=risk,
                        spec_sha=spec_sha,
                    )

            self._finish_phase(gates_phase_id, PhaseStatus.FAIL, error=str(failing.gate))
            if self.corrections < constants.SPEC_CORRECTION_CAP:
                self.corrections += 1
                correction = failing.correction_text()
                continue
            return self._blocked(
                BlockedReason.SPEC_GATE, failing.correction_text(), phase_id=gates_phase_id
            )

    async def _finalize_spec(self, risk: RiskBreakdown | None, *, attempt: int) -> str:
        """Risk header, task row and label, then the spec commit the approval binds to."""
        spec_path = self.worktree.path.joinpath(*self.spec_rel.split("/"))
        if risk is not None and risk.risk_class is not None:
            risk_phase = self._start_phase(
                PhaseName.RISK, PhaseKind.CODE, "orchestrator", attempt=attempt, timeout_s=60
            )
            spec_path.write_text(
                write_risk_header(spec_path.read_text(encoding="utf-8"), risk.risk_class),
                encoding="utf-8",
                newline="\n",
            )
            self.writer.set_task_risk(
                self.task_id,
                risk=str(risk.risk_class),
                score=risk.score,
                governed_path=risk.governed_path,
            )
            if self.github is not None:
                await self.github.add_labels(
                    self.issue.number, [str(risk.risk_class)], task_id=self.task_id
                )
            self._finish_phase(risk_phase, PhaseStatus.PASS)
        commit_phase = self._start_phase(
            PhaseName.COMMIT, PhaseKind.CODE, "orchestrator", attempt=attempt, timeout_s=60
        )
        suffix = f" ({risk.risk_class})" if risk is not None and risk.risk_class else ""
        message = f"spec: {self.spec_rel}{suffix}"
        commit_all(self.worktree.path, message, settings=self.deps.settings)
        sha = blob_sha(self.worktree.path, self.spec_rel)
        self.writer.set_task_spec(self.task_id, spec_path=self.spec_rel, spec_sha=sha)
        self._event(
            EventKind.HANDOFF, {"spec": self.spec_rel, "spec_sha": sha}, phase_id=commit_phase
        )
        self._finish_phase(commit_phase, PhaseStatus.PASS)
        return sha
