"""The task runner: plan → approval → build and review → deliver in one run (PRD §Architecture).

The supervisor calls ``TaskRunner.run`` for every task the scheduler
starts. The entry point comes from the trace (TRD §7.8): a recorded PR →
deliver; no committed spec → plan; a block whose cause was a human
omission → plan after the cause check; otherwise approval and build. A
block posts the Blocked-Comment unless the phase already told the human.
"""

import asyncio
import json
import sqlite3
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from orchestrator import constants
from orchestrator.budget.keys import PhaseKeys
from orchestrator.clock import parse_ts
from orchestrator.errors import FactoryDisabledError
from orchestrator.github.client import IssueComment
from orchestrator.harness.base import HarnessAdapter
from orchestrator.harness.claude_code import Runner
from orchestrator.harness.process import run_with_timeout
from orchestrator.harness.registry import read_harness_versions
from orchestrator.instruments.risk import RiskBreakdown, RiskInstrument
from orchestrator.instruments.verdict import ReviewPolicy, VerdictScore
from orchestrator.intake import messages
from orchestrator.intake.ci import pending_reentry
from orchestrator.intake.issue_fetcher import TaggedIssue, fetch_issue
from orchestrator.issue_to_pr import PipelineOutcome, build_and_review
from orchestrator.models.config import FactoryConfig
from orchestrator.models.enums import (
    AgentRole,
    BlockedReason,
    EventKind,
    RiskClass,
    RunStatus,
    TaskStatus,
)
from orchestrator.models.envelopes import VerifyOutput
from orchestrator.models.settings import Settings
from orchestrator.phases.approval import PlanApprovalPhase, PlanApprovals, gate_for
from orchestrator.phases.build import BuildDeps, BuildOutcome, Sleep
from orchestrator.phases.deliver import DeliverPhase
from orchestrator.phases.full_suite import FullSuitePhase
from orchestrator.phases.plan import HoldoutPort, PlanPhase, spec_path_for
from orchestrator.trace.writer import TraceWriter
from orchestrator.version import factory_version
from orchestrator.workspace.container import DockerCli, TaskContainer, create_container
from orchestrator.workspace.git import GitError, head_sha
from orchestrator.workspace.hooks import install_codex_rules_in, verify_hook
from orchestrator.workspace.worktree import Worktree, blob_sha, create_worktree

ResumePoint = Literal["plan", "approval", "build", "review", "deliver"]
HarnessFactory = Callable[[AgentRole, Runner, Mapping[str, str]], HarnessAdapter]
DepsFor = Callable[[AgentRole], BuildDeps]
TokenFor = Callable[[str], Awaitable[str | None]]
HUMAN_CAUSE = frozenset(
    {
        BlockedReason.OPEN_QUESTIONS,
        BlockedReason.MISSING_HOLDOUT,
        BlockedReason.HUMAN_DECISION_NEEDED,
        BlockedReason.SPEC_GATE,
    }
)
PLAN_NOTIFIES = frozenset({BlockedReason.OPEN_QUESTIONS, BlockedReason.MISSING_HOLDOUT})
MISSING_PREFIX = "holdout tests missing: "


class RunnerGitHub(Protocol):
    async def get_issue(self, number: int, *, task_id: str) -> dict[str, Any]: ...

    async def list_issue_comments(self, number: int, *, task_id: str) -> list[IssueComment]: ...

    async def collaborator_permission(self, login: str, *, task_id: str) -> str: ...

    async def create_comment(self, number: int, body: str, *, task_id: str) -> int: ...

    async def add_labels(self, number: int, labels: Sequence[str], *, task_id: str) -> None: ...

    async def remove_label(self, number: int, label: str, *, task_id: str) -> bool: ...

    async def list_labels(self, number: int, *, task_id: str) -> list[str]: ...

    async def get_branch_sha(self, branch: str, *, task_id: str) -> str | None: ...

    async def list_pulls_for_branch(self, head: str, *, task_id: str) -> list[dict[str, Any]]: ...

    async def create_pull(
        self, *, title: str, head: str, base: str, body: str, task_id: str
    ) -> dict[str, Any]: ...

    async def request_reviewers(
        self, number: int, reviewers: Sequence[str], *, task_id: str, teams: Sequence[str] = ()
    ) -> None: ...


@dataclass
class RunnerDeps:
    writer: TraceWriter
    config: FactoryConfig
    settings: Settings
    harnesses: Mapping[AgentRole, HarnessAdapter]
    github: RunnerGitHub
    repo_clone: Path
    holdout: HoldoutPort
    approvals: PlanApprovals
    base_branch: str
    token_for: TokenFor | None = None
    sleep: Sleep = asyncio.sleep
    factory_version: str = field(default_factory=factory_version)
    keys: PhaseKeys = field(default_factory=PhaseKeys)
    docker: DockerCli | None = None
    task_image: str | None = None
    harness_for: HarnessFactory | None = None

    def build_deps(
        self,
        role: AgentRole,
        runner: Runner | None = None,
        versions: Mapping[str, str] | None = None,
    ) -> BuildDeps:
        harness = self.harnesses[role]
        if runner is not None and self.harness_for is not None:
            harness = self.harness_for(role, runner, dict(versions or {}))
        return BuildDeps(
            writer=self.writer,
            config=self.config,
            settings=self.settings,
            harness=harness,
            sleep=self.sleep,
            factory_version=self.factory_version,
            runner=runner or run_with_timeout,
            keys=self.keys,
            hooks_dir=(
                constants.HOOKS_MOUNT if runner is not None else str(self.settings.hooks_dir)
            ),
            hook_python="python3" if runner is not None else sys.executable,
        )


@dataclass(frozen=True)
class LastBlock:
    reason: BlockedReason
    evidence: str
    at: str


@dataclass(frozen=True)
class RunReport:
    status: Literal["pr_open", "merged", "blocked", "cancelled"]
    task_id: str
    run_id: str
    entered_at: ResumePoint
    blocked_reason: BlockedReason | None = None
    evidence: str = ""
    notified: bool = False

    @property
    def delivered(self) -> bool:
        return self.status == "pr_open"


def _amount(payload: dict[str, object], key: str) -> float:
    value = payload.get(key)
    return float(value) if isinstance(value, int | float) else 0.0


def last_block(writer: TraceWriter, task_id: str) -> LastBlock | None:
    row = writer.db.fetchone(
        "SELECT created_at, payload_json FROM events WHERE task_id = ? AND kind = 'error'"
        " AND payload_json LIKE '%blocked_reason%' ORDER BY seq DESC LIMIT 1",
        (task_id,),
    )
    if row is None or row["payload_json"] is None:
        return None
    payload = json.loads(row["payload_json"])
    try:
        reason = BlockedReason(str(payload.get("blocked_reason")))
    except ValueError:
        return None
    return LastBlock(reason, str(payload.get("evidence") or ""), str(row["created_at"]))


def last_verdict(writer: TraceWriter, task_id: str) -> str | None:
    row = writer.db.fetchone(
        "SELECT payload_json FROM events WHERE task_id = ? AND kind = 'log'"
        " AND payload_json LIKE '%verdict_policy%' ORDER BY seq DESC LIMIT 1",
        (task_id,),
    )
    if row is None or row["payload_json"] is None:
        return None
    verdict = json.loads(row["payload_json"]).get("verdict_policy", {}).get("verdict")
    return str(verdict) if verdict else None


def missing_holdout_ids(evidence: str) -> list[str]:
    if MISSING_PREFIX not in evidence:
        return []
    tail = evidence.split(MISSING_PREFIX, 1)[1].splitlines()[0]
    return [t.strip() for t in tail.split(",") if t.strip()]


class TaskRunner:
    def __init__(self, deps: RunnerDeps) -> None:
        self.deps = deps
        self.writer = deps.writer
        self.instrument = RiskInstrument.from_yaml(deps.config.risk_yaml)
        self.policy = ReviewPolicy.from_yaml(deps.config.review_policy_yaml)

    # --- where to enter -----------------------------------------------------------------

    def resume_point(
        self, task: sqlite3.Row, previous: LastBlock | None, *, head_sha: str | None = None
    ) -> ResumePoint:
        task_id = str(task["task_id"])
        if task["pr_number"] is not None:
            return "build" if pending_reentry(self.writer, task_id) is not None else "deliver"
        if not task["spec_sha"]:
            return "plan"
        if previous is not None and previous.reason in HUMAN_CAUSE:
            return "plan"
        if last_verdict(self.writer, task_id) == "accepted" and (
            previous is None or previous.reason is BlockedReason.TIMEOUT
        ):
            return "deliver"
        committed = self.writer.last_build_commit(task_id)
        if (
            committed is not None
            and committed == head_sha
            and (
                previous is None
                or previous.reason
                in (
                    BlockedReason.TIMEOUT,
                    BlockedReason.REVISION_CAP,
                    # TRD §7.8 groups repair_cap | full_suite | timeout: a new attempt on the
                    # same branch head. A build that passed its gates and was committed is
                    # reviewed and re-checked; rebuilding it only makes the builder report
                    # that nothing changed, which blocked again (mdedit, 2026-10-25).
                    BlockedReason.REPAIR_CAP,
                    BlockedReason.FULL_SUITE,
                )
            )
        ):
            return "review"  # the build is on the branch: no second build (TRD §7.8)
        return "approval" if gate_for(task["risk"]) is not None else "build"

    async def _cause_unchanged(
        self, task: sqlite3.Row, previous: LastBlock | None, worktree: Worktree, issue: TaggedIssue
    ) -> str | None:
        """Why the task stays blocked, or ``None`` when the human changed the cause."""
        if previous is None or previous.reason not in HUMAN_CAUSE:
            return None
        if previous.reason in (BlockedReason.OPEN_QUESTIONS, BlockedReason.SPEC_GATE):
            since = parse_ts(previous.at)
            answered = any(
                parse_ts(c.created_at) >= since and not c.author_login.endswith("[bot]")
                for c in issue.comments
            )
            if answered:
                return None
            return (
                "No answer was posted in the issue after the block; the task stays blocked"
                " until a person with write access replies and removes the label again."
            )
        if previous.reason is BlockedReason.MISSING_HOLDOUT:
            self.deps.holdout.refresh()
            missing = [
                t
                for t in missing_holdout_ids(previous.evidence)
                if not self.deps.holdout.has_test(t)
            ]
            if not missing:
                return None
            listed = ", ".join(missing)
            return f"The holdout tests are still missing: {listed}; the task stays blocked."
        recorded = str(task["spec_sha"] or "")
        spec_rel = task["spec_path"]
        if not recorded or not spec_rel:
            return None
        try:
            current = blob_sha(worktree.path, str(spec_rel))
        except GitError:
            return None
        if current != recorded:
            return None
        return (
            "The spec on the task branch is unchanged; the human decision is a spec edit, so"
            " the task stays blocked."
        )

    # --- the run ---------------------------------------------------------------------

    async def run(self, task_id: str) -> RunReport:
        task = self.writer.get_task(task_id)
        if task is None:
            raise ValueError(f"task {task_id} is not admitted")
        if task["status"] == str(TaskStatus.MERGED):
            return RunReport("merged", task_id, "", "deliver")
        previous = last_block(self.writer, task_id)
        issue_number = int(task["issue_number"])
        try:
            worktree = create_worktree(
                self.deps.repo_clone,
                issue=issue_number,
                worktrees_dir=self.deps.settings.worktrees_dir,
            )
            findings = await self._reconcile(task, worktree)
            refreshed = self.writer.get_task(task_id)  # the reconciliation may have adopted a PR
            task = refreshed if refreshed is not None else task
            point = self.resume_point(task, previous, head_sha=head_sha(worktree.path))
        except Exception as exc:  # before the run row exists: block with evidence, never hang
            return await self._blocked_before_run(task_id, issue_number, exc)
        attempt = int(task["attempt"])
        if self.writer.run_for_attempt(task_id, attempt) is not None:
            attempt += 1  # a start without the sweep or a resume: the next attempt
            self.writer.set_task_attempt(task_id, attempt)
        run_id = self.writer.start_run(
            task_id=task_id,
            attempt=attempt,
            factory_version=self.deps.factory_version,
            config_sha=self.deps.config.config_sha,
        )
        if task["status"] != str(TaskStatus.RUNNING):
            self.writer.set_task_status(task_id, TaskStatus.RUNNING)
        self.writer.write_event(
            EventKind.LOG,
            "orchestrator",
            task_id=task_id,
            run_id=run_id,
            payload={
                "runner": "start",
                "entered_at": point,
                "attempt": attempt,
                "previous_block": None if previous is None else str(previous.reason),
            },
        )
        self.writer.write_event(
            EventKind.LOG, "orchestrator", task_id=task_id, run_id=run_id, payload=findings
        )
        container: TaskContainer | None = None
        try:
            versions: dict[str, str] = {}
            if self.deps.settings.workspace == "container":
                container, versions = await self._start_container(task, worktree, run_id)
            runner: Runner | None = None
            if container is not None and self.deps.docker is not None:
                runner = self.deps.docker.exec_runner(container)

            def deps_for(role: AgentRole) -> BuildDeps:
                return self.deps.build_deps(role, runner, versions)

            return await self._run(task, worktree, run_id, point, previous, deps_for)
        except asyncio.CancelledError as exc:
            why = str(exc.args[0]) if exc.args else ""
            self.writer.close_open_work(
                task_id, error="kill_switch" if why == "kill_switch" else "cancelled"
            )
            raise
        except FactoryDisabledError as exc:
            return await self._cancelled(task_id, issue_number, run_id, str(exc), point=point)
        except Exception as exc:
            self.writer.close_open_work(task_id, error=str(exc)[:200])
            evidence = f"unexpected error: {type(exc).__name__}: {exc}"
            return await self._blocked(
                task_id,
                issue_number,
                run_id,
                BlockedReason.TIMEOUT,
                evidence,
                phase=None,
                point=point,
                notified=False,
            )
        finally:
            if container is not None and self.deps.docker is not None:
                await self.deps.docker.remove(container.name)
                self.writer.write_event(
                    EventKind.LOG,
                    "orchestrator",
                    task_id=task_id,
                    run_id=run_id,
                    payload={"container": container.name, "removed": True},
                )

    async def _reconcile(self, task: sqlite3.Row, worktree: Worktree) -> dict[str, object]:
        """Read GitHub before acting (TRD §7.8): the branch's PRs, the labels, the remote head."""
        task_id = str(task["task_id"])
        github = self.deps.github
        findings: dict[str, object] = {"reconcile": "github", "branch": worktree.branch}
        try:
            pulls = await github.list_pulls_for_branch(worktree.branch, task_id=task_id)
        except Exception as exc:
            pulls = []
            findings["pulls_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        open_prs = [p for p in pulls if p.get("state") == "open"]
        findings["open_prs"] = [int(p["number"]) for p in open_prs]
        if task["pr_number"] is None and open_prs:
            pull = open_prs[0]
            head = pull.get("head")
            sha = head.get("sha") if isinstance(head, dict) else None
            self.writer.set_task_pr(
                task_id,
                pr_number=int(pull["number"]),
                head_sha=sha if isinstance(sha, str) else None,
            )
            findings["pr_adopted"] = int(pull["number"])
        try:
            findings["labels"] = await github.list_labels(
                int(task["issue_number"]), task_id=task_id
            )
        except Exception as exc:
            findings["labels_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        try:
            findings["remote_sha"] = await github.get_branch_sha(worktree.branch, task_id=task_id)
        except Exception as exc:
            findings["remote_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        findings["local_sha"] = head_sha(worktree.path)
        return findings

    def _real_binaries(self) -> dict[str, str]:
        """The CLI binaries a task container must report versions for (fakes need none)."""
        roster = self.deps.config.roster
        binaries: dict[str, str] = {}
        for role in AgentRole:
            cfg = roster.role(role)
            if self.deps.harnesses[role].name == "fake" or cfg.harness == "fake":
                continue
            if cfg.harness == "claude_code":
                binaries["claude_code"] = self.deps.settings.claude_bin
            elif cfg.harness == "codex":
                binaries["codex"] = self.deps.settings.codex_bin
        return binaries

    async def _start_container(
        self, task: sqlite3.Row, worktree: Worktree, run_id: str
    ) -> tuple[TaskContainer, dict[str, str]]:
        docker, image = self.deps.docker, self.deps.task_image
        if docker is None or image is None:
            raise ValueError("the container workspace needs a docker client and a task image")
        name = f"factory-task-{int(task['issue_number'])}-{int(task['attempt'])}"
        container = await create_container(
            docker,
            image=image,
            name=name,
            worktree=worktree,
            network=self.deps.settings.docker_network,
            task_id=str(task["task_id"]),
            hooks_dir=self.deps.settings.hooks_dir,
        )
        roster = self.deps.config.roster
        codex_rules_at: str | None = None
        try:  # fail closed: no phase runs in a container whose hook does not deny
            await verify_hook(
                docker.exec_runner(container),
                hooks_dir=constants.HOOKS_MOUNT,
                python="python3",
                cwd=worktree.path,
            )
            if any(r.harness == "codex" for r in (roster.planner, roster.builder, roster.reviewer)):
                codex_rules_at = await install_codex_rules_in(
                    docker.exec_runner(container), cwd=worktree.path
                )
            versions = await read_harness_versions(
                docker.exec_runner(container), binaries=self._real_binaries(), cwd=worktree.path
            )
        except Exception:
            await docker.remove(name)
            raise
        self.writer.write_event(
            EventKind.LOG,
            "orchestrator",
            task_id=str(task["task_id"]),
            run_id=run_id,
            payload={
                "container": name,
                "image": image,
                "network": self.deps.settings.docker_network,
                "hooks": constants.HOOKS_MOUNT,
                "codex_rules": codex_rules_at,
                "harness_versions": versions,
            },
        )
        return container, versions

    async def _run(
        self,
        task: sqlite3.Row,
        worktree: Worktree,
        run_id: str,
        point: ResumePoint,
        previous: LastBlock | None,
        deps_for: DepsFor,
    ) -> RunReport:
        task_id = str(task["task_id"])
        issue_number = int(task["issue_number"])
        entered = point
        github = self.deps.github
        issue = await fetch_issue(github, issue_number, task_id=task_id)
        spec_rel = str(task["spec_path"] or spec_path_for(issue_number, issue.title))
        risk_class: str | None = task["risk"]
        governed_path: str | None = task["governed_path"]
        risk: RiskBreakdown | None = None
        verdict: VerdictScore | None = None
        pipeline: PipelineOutcome | None = None

        if point == "plan":
            stay = await self._cause_unchanged(task, previous, worktree, issue)
            if stay is not None:
                assert previous is not None
                return await self._blocked(
                    task_id,
                    issue_number,
                    run_id,
                    previous.reason,
                    stay,
                    phase="plan",
                    point=entered,
                    notified=False,
                )
            plan = await PlanPhase(
                deps_for(AgentRole.PLANNER),
                task_id=task_id,
                worktree=worktree,
                issue=issue,
                run_id=run_id,
                holdout=self.deps.holdout,
                github=github,
                instrument=self.instrument,
            ).run()
            if not plan.written:
                reason = plan.blocked_reason or BlockedReason.SPEC_GATE
                return await self._blocked(
                    task_id,
                    issue_number,
                    run_id,
                    reason,
                    plan.evidence,
                    phase="plan",
                    point=entered,
                    notified=reason in PLAN_NOTIFIES,
                )
            spec_rel = plan.spec_rel
            risk = plan.risk
            risk_class = None if risk is None or risk.risk_class is None else str(risk.risk_class)
            governed_path = None if risk is None else risk.governed_path
            point = "approval"

        if point == "approval":
            if gate_for(risk_class) is not None:
                current = self.writer.get_task(task_id)
                assert current is not None
                breakdown = risk or RiskBreakdown.recorded(
                    RiskClass(str(risk_class)), float(current["risk_score"] or 0.0), governed_path
                )
                spec_text = worktree.path.joinpath(*spec_rel.split("/")).read_text(encoding="utf-8")
                await PlanApprovalPhase(
                    writer=self.writer,
                    github=github,
                    approvals=self.deps.approvals,
                    task_id=task_id,
                    issue=issue,
                    run_id=run_id,
                    risk=breakdown,
                    spec_rel=spec_rel,
                    spec_sha=str(current["spec_sha"] or ""),
                    spec_text=spec_text,
                    attempt=int(task["attempt"]),
                    seq_start=self.writer.last_phase_seq(run_id),
                ).run()
            point = "build"

        if point in ("build", "review"):
            initial_verify: VerifyOutput | None = None
            repairs_used = 0
            reentry = (
                pending_reentry(self.writer, task_id) if task["pr_number"] is not None else None
            )
            if reentry is not None:
                payload = json.loads(reentry["payload_json"])
                cause = f"{payload.get('cause')}: {payload.get('detail')}"
                resume = self.writer.latest_control(task_id, "resume")
                since = int(resume["seq"]) if resume is not None else 0
                repairs_used = self.writer.count_controls(task_id, "ci_reentry", since_seq=since)
                if repairs_used > constants.REPAIR_CAP:
                    return await self._blocked(
                        task_id,
                        issue_number,
                        run_id,
                        BlockedReason.REPAIR_CAP,
                        f"{cause}; CI re-entry {repairs_used} exceeds the repair cap"
                        f" {constants.REPAIR_CAP}",
                        phase="ci_wait",
                        point=entered,
                        notified=False,
                    )
                initial_verify = VerifyOutput(
                    status="fail",
                    gate="ci",
                    violations=[cause],
                    output_tail=str(payload.get("detail") or ""),
                )
            prior_build: BuildOutcome | None = None
            if point == "review":
                committed = self.writer.last_build_commit(task_id)
                prior_build = BuildOutcome("pass", run_id, committed, None, "", 0, 0, 0.0)
            pipeline = await build_and_review(
                deps_for(AgentRole.BUILDER),
                task_id=task_id,
                worktree=worktree,
                spec_rel=spec_rel,
                run_id=run_id,
                github=github,
                issue_number=issue_number,
                policy=self.policy,
                review_deps=deps_for(AgentRole.REVIEWER),
                initial_verify=initial_verify,
                repairs_used=repairs_used,
                prior_build=prior_build,
            )
            if not pipeline.accepted:
                reason = pipeline.blocked_reason or BlockedReason.TIMEOUT
                return await self._blocked(
                    task_id,
                    issue_number,
                    run_id,
                    reason,
                    pipeline.evidence,
                    phase=self._last_failed_phase(run_id),
                    point=entered,
                    notified=pipeline.notified,
                )
            verdict = pipeline.verdict
            point = "deliver"

        # The full suite with the holdout runs on the SHA that gets pushed (TRD §7.5).
        suite = await FullSuitePhase(
            deps_for(AgentRole.BUILDER),
            task_id=task_id,
            worktree=worktree,
            spec_rel=spec_rel,
            run_id=run_id,
            holdout=self.deps.holdout,
            issue_number=issue_number,
            attempt=int(task["attempt"]),
            build_repairs=0 if pipeline is None else pipeline.build.repairs,
            docker=self.deps.docker,
            task_image=self.deps.task_image,
            network=self.deps.settings.docker_network,
        ).run()
        if not suite.passed:
            reason = suite.blocked_reason or BlockedReason.FULL_SUITE
            return await self._blocked(
                task_id,
                issue_number,
                run_id,
                reason,
                suite.evidence,
                phase=(
                    "full_suite"
                    if reason is BlockedReason.FULL_SUITE
                    else self._last_failed_phase(run_id)
                ),
                point=entered,
                notified=False,
            )

        token = await self.deps.token_for(task_id) if self.deps.token_for is not None else None
        governed = risk_class == str(RiskClass.GOVERNED) and governed_path is not None
        owners = self.deps.approvals.owners_of(str(governed_path)) if governed else ()
        delivered = await DeliverPhase(
            deps_for(AgentRole.BUILDER),
            task_id=task_id,
            worktree=worktree,
            spec_rel=spec_rel,
            run_id=run_id,
            github=github,
            base_branch=self.deps.base_branch,
            verdict=verdict,
            token=token,
            owners=owners,
        ).run()
        if delivered.status == "blocked":
            return await self._blocked(
                task_id,
                issue_number,
                run_id,
                delivered.blocked_reason or BlockedReason.TIMEOUT,
                delivered.evidence,
                phase="deliver",
                point=entered,
                notified=False,
            )
        self.writer.finish_run(run_id, RunStatus.SUCCESS)
        return RunReport(delivered.status, task_id, run_id, entered)

    # --- the kill-switch ----------------------------------------------------------------

    async def _cancelled(
        self, task_id: str, issue_number: int, run_id: str, detail: str, *, point: ResumePoint
    ) -> RunReport:
        """A phase start was refused by the flag: the task is cancelled, the branch stays."""
        last_phase = self.writer.last_completed_phase(task_id)
        self.writer.close_open_work(task_id, error="kill_switch")
        self.writer.set_task_status(task_id, TaskStatus.CANCELLED)
        self.writer.write_event(
            EventKind.LOG,
            "orchestrator",
            task_id=task_id,
            run_id=run_id,
            payload={
                "kill_switch": "phase_refused",
                "detail": detail,
                "last_completed_phase": last_phase,
            },
        )
        await self.deps.github.create_comment(
            issue_number,
            messages.cancelled(last_phase, cause=messages.CANCEL_CAUSE_KILL_SWITCH),
            task_id=task_id,
        )
        self.writer.finish_run(run_id, RunStatus.FAIL)
        return RunReport("cancelled", task_id, run_id, point)

    # --- blocks -------------------------------------------------------------------------

    def _last_failed_phase(self, run_id: str) -> str | None:
        failed = [r for r in self.writer.phases_for_run(run_id) if r["status"] == "fail"]
        return str(failed[-1]["name"]) if failed else None

    async def _blocked(
        self,
        task_id: str,
        issue_number: int,
        run_id: str,
        reason: BlockedReason,
        evidence: str,
        *,
        phase: str | None,
        point: ResumePoint,
        notified: bool,
    ) -> RunReport:
        task = self.writer.get_task(task_id)
        if task is None or task["status"] != str(TaskStatus.BLOCKED):
            self.writer.set_task_status(task_id, TaskStatus.BLOCKED, blocked_reason=reason)
            self.writer.write_event(
                EventKind.ERROR,
                "orchestrator",
                task_id=task_id,
                run_id=run_id,
                payload={
                    "blocked_reason": str(reason),
                    "evidence": evidence[-constants.OUTPUT_TAIL_CHARS :],
                },
            )
        if not notified:
            await self._notify(task_id, issue_number, run_id, reason, evidence, phase)
        self.writer.finish_run(run_id, RunStatus.FAIL)
        return RunReport("blocked", task_id, run_id, point, reason, evidence, True)

    async def _blocked_before_run(
        self, task_id: str, issue_number: int, exc: Exception
    ) -> RunReport:
        """A failure before the run row (workspace, reconciliation): the task blocks with
        evidence and the issue hears about it, instead of staying ``running`` with no trace."""
        evidence = f"unexpected error before the run: {type(exc).__name__}: {exc}"
        reason = BlockedReason.TIMEOUT
        self.writer.set_task_status(task_id, TaskStatus.BLOCKED, blocked_reason=reason)
        self.writer.write_event(
            EventKind.ERROR,
            "orchestrator",
            task_id=task_id,
            payload={
                "blocked_reason": str(reason),
                "evidence": evidence[-constants.OUTPUT_TAIL_CHARS :],
            },
        )
        body = messages.blocked(
            reason,
            phase=None,
            summary=evidence.splitlines()[0][:300],
            command=None,
            exit_code=None,
            output_tail=None,
        )
        await self.deps.github.create_comment(issue_number, body, task_id=task_id)
        await self.deps.github.add_labels(
            issue_number, [constants.LABEL_NEEDS_HUMAN], task_id=task_id
        )
        return RunReport("blocked", task_id, "", "plan", reason, evidence, True)

    async def _notify(
        self,
        task_id: str,
        issue_number: int,
        run_id: str,
        reason: BlockedReason,
        evidence: str,
        phase: str | None,
    ) -> None:
        failed = [r for r in self.writer.gate_results_for_run(run_id) if not int(r["passed"])]
        last = failed[-1] if failed else None
        summary = evidence.strip().splitlines()[0][:300] if evidence.strip() else str(reason)
        body = messages.blocked(
            reason,
            phase=phase,
            summary=summary,
            command=None if last is None else last["command"],
            exit_code=None if last is None else last["exit_code"],
            output_tail=None if last is None else last["output_tail"],
        )
        labels = [constants.LABEL_NEEDS_HUMAN]
        if reason is BlockedReason.BUDGET:
            stop = self.writer.latest_budget_stop(task_id)
            if stop is not None:
                body = messages.budget_stop(
                    task_spent=_amount(stop, "task_spent_usd"),
                    task_budget=_amount(stop, "task_budget_usd"),
                    phase_cap=_amount(stop, "phase_cap_usd"),
                    daily_spent=_amount(stop, "daily_spent_usd"),
                    daily_cap=_amount(stop, "daily_cap_usd"),
                )
            labels = [constants.LABEL_BUDGET, constants.LABEL_NEEDS_HUMAN]
        await self.deps.github.create_comment(issue_number, body, task_id=task_id)
        await self.deps.github.add_labels(issue_number, labels, task_id=task_id)
