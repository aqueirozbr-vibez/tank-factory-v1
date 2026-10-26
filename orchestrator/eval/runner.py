"""Replay an eval set on ``eval/scratch`` and score it (PRD §Capability 5 · Eval set; TRD §4.2).

Each item is a closed issue replayed through the real pipeline, plan →
build → review → full suite, on a worktree of ``eval/scratch`` cut from
the base branch, with no human in the loop (no comments, no labels, no
approval wait) and nothing pushed. The phases write their rows into a
scratch trace under the run's directory, so the installation's tasks and
KPIs stay clean; the installation's trace keeps the ``eval_runs`` and
``eval_results`` rows. The pass rate is reported overall and per class; a
drop of more than ``EVAL_BLOCK_DROP_POINTS`` against the baseline (the
latest complete run of the set, or the run named) marks the run blocked.
"""

import json
import sys
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from orchestrator import constants
from orchestrator.budget.keys import PhaseKeys
from orchestrator.errors import FactoryError
from orchestrator.harness.base import HarnessAdapter
from orchestrator.instruments.risk import RiskInstrument
from orchestrator.instruments.verdict import ReviewPolicy
from orchestrator.intake.issue_fetcher import TaggedIssue
from orchestrator.issue_to_pr import build_and_review
from orchestrator.models.config import FactoryConfig
from orchestrator.models.enums import AgentRole, EventKind, RunStatus, TaskStatus, WorkspaceKind
from orchestrator.models.settings import Settings
from orchestrator.phases.build import BuildDeps
from orchestrator.phases.full_suite import FullSuitePhase, HoldoutSource
from orchestrator.phases.plan import HoldoutPort, PlanPhase
from orchestrator.trace import TraceDB, TraceWriter
from orchestrator.workspace.git import run_git
from orchestrator.workspace.worktree import create_worktree, remove_worktree

EvalClass = Literal["bug", "test", "docs", "feature"]
EVAL_CLASSES: tuple[str, ...] = ("bug", "test", "docs", "feature")
Trigger = Literal["install_dry_run", "config_pr", "factory_upgrade", "manual", "harness_upgrade"]
IssueSource = Callable[["EvalItem"], Awaitable[TaggedIssue]]


class EvalError(FactoryError):
    code = "eval_error"


@dataclass(frozen=True)
class EvalItem:
    id: str
    issue_number: int
    eval_class: str
    title: str
    merged_pr: int | None = None


@dataclass(frozen=True)
class ItemResult:
    item: EvalItem
    passed: bool
    cost_usd: float
    duration_s: int
    failure_reason: str | None


@dataclass(frozen=True)
class EvalReport:
    run_id: str
    set_id: str
    pass_rate: float
    per_class: dict[str, float]
    baseline_run_id: str | None
    baseline_pass_rate: float | None
    delta_points: float | None
    blocked: bool
    results: tuple[ItemResult, ...]
    reference_pass_rate: float | None = None  # the pilot's rate, entered by the operator
    gap_points: float | None = None  # pass rate minus the reference; never blocks

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.results if r.passed)


def pass_rates(results: Sequence[ItemResult]) -> tuple[float, dict[str, float]]:
    """The pass rate in points (0 to 100) overall and per class present in the set."""
    if not results:
        return 0.0, {}
    overall = 100.0 * sum(1 for r in results if r.passed) / len(results)
    per_class: dict[str, float] = {}
    for eval_class in EVAL_CLASSES:
        members = [r for r in results if r.item.eval_class == eval_class]
        if members:
            per_class[eval_class] = round(
                100.0 * sum(1 for r in members if r.passed) / len(members), 2
            )
    return round(overall, 2), per_class


def delta_points(current: float, baseline: float | None) -> float | None:
    return None if baseline is None else round(current - baseline, 2)


def ensure_eval_set(
    writer: TraceWriter,
    *,
    name: str,
    repo: str,
    items: Sequence[tuple[int, str, int | None]],
    reference_pass_rate: float | None = None,
) -> str:
    """The set named ``name`` with exactly these items (idempotent); returns its id."""
    for _issue, eval_class, _pr in items:
        if eval_class not in EVAL_CLASSES:
            raise EvalError(f"eval class {eval_class!r} is not one of {', '.join(EVAL_CLASSES)}")
    existing = writer.find_eval_set(name)
    set_id = (
        str(existing["id"])
        if existing is not None
        else writer.create_eval_set(name=name, repo=repo, reference_pass_rate=reference_pass_rate)
    )
    for issue, eval_class, merged_pr in items:
        writer.add_eval_item(set_id, issue_number=issue, eval_class=eval_class, merged_pr=merged_pr)
    return set_id


class EvalRunner:
    def __init__(
        self,
        *,
        writer: TraceWriter,
        settings: Settings,
        config: FactoryConfig,
        repo_clone: Path,
        harnesses: Mapping[AgentRole, HarnessAdapter],
        holdout: HoldoutPort,
        issues: IssueSource,
        base_branch: str = "main",
        factory_version: str = "0.0.0+local",
        keys: PhaseKeys | None = None,
    ) -> None:
        self.writer = writer
        self.settings = settings
        self.config = config
        self.repo_clone = repo_clone
        self.harnesses = harnesses
        self.holdout = holdout
        self.issues = issues
        self.base_branch = base_branch
        self.factory_version = factory_version
        self.keys = keys or PhaseKeys()
        self.instrument = RiskInstrument.from_yaml(config.risk_yaml)
        self.policy = ReviewPolicy.from_yaml(config.review_policy_yaml)

    # --- the scratch trace one run writes its phases into ---------------------------------------

    def _scratch(self, run_dir: Path, items: int) -> tuple[TraceDB, TraceWriter, float]:
        installation = self.writer.get_installation()
        if installation is None:
            raise EvalError("the installation row is missing; run `factory install` first")
        db = TraceDB.open(run_dir / "trace.db")
        scratch = TraceWriter(db)
        scratch.load_config_version(**self.config.config_version_row())
        scratch.insert_installation(
            org=str(installation["org"]),
            repo=str(installation["repo"]),
            factory_version=self.factory_version,
            config_sha=self.config.config_sha,
            per_task_budget_usd=float(installation["per_task_budget_usd"]),
            export_pubkey="eval",
            concurrency_n=int(installation["concurrency_n"]),
            timezone=str(installation["timezone"]),
        )
        # the replay's own ceiling: items x the per-task budget, not the day's cap (spec 67)
        cap_usd = round(items * float(installation["per_task_budget_usd"]), 4)
        scratch.set_daily_cap(cap_usd)
        return db, scratch, cap_usd

    def _deps(self, scratch: TraceWriter, role: AgentRole) -> BuildDeps:
        return BuildDeps(
            writer=scratch,
            config=self.config,
            settings=self.settings,
            harness=self.harnesses[role],
            factory_version=self.factory_version,
            keys=self.keys,
            hooks_dir=str(self.settings.hooks_dir),
            hook_python=sys.executable,
        )

    # --- one item --------------------------------------------------------------------------------

    async def _replay(self, scratch: TraceWriter, run_dir: Path, item: EvalItem) -> ItemResult:
        installation = scratch.get_installation()
        assert installation is not None
        task_id = f"{installation['org']}/{installation['repo']}#{item.issue_number}"
        started = time.monotonic()
        scratch.insert_task(
            task_id=task_id,
            issue_number=item.issue_number,
            title=item.title,
            branch=constants.EVAL_BRANCH,
            workspace_kind=WorkspaceKind.WORKTREE,
            budget_usd=float(installation["per_task_budget_usd"]),
            admitted_by="eval",
            config_sha=self.config.config_sha,
            status=TaskStatus.RUNNING,
        )
        run_git(["branch", "-D", constants.EVAL_BRANCH], cwd=self.repo_clone, check=False)
        worktree = create_worktree(
            self.repo_clone,
            issue=item.issue_number,
            worktrees_dir=run_dir / "worktrees",
            base=self.base_branch,
            branch=constants.EVAL_BRANCH,
        )
        reason: str | None = None
        try:
            run_id = scratch.start_run(
                task_id=task_id,
                attempt=1,
                factory_version=self.factory_version,
                config_sha=self.config.config_sha,
            )
            issue = await self.issues(item)
            plan = await PlanPhase(
                self._deps(scratch, AgentRole.PLANNER),
                task_id=task_id,
                worktree=worktree,
                issue=issue,
                run_id=run_id,
                holdout=self.holdout,
                github=None,
                instrument=self.instrument,
            ).run()
            if not plan.written:
                reason = f"plan: {plan.blocked_reason}"
            else:
                pipeline = await build_and_review(
                    self._deps(scratch, AgentRole.BUILDER),
                    task_id=task_id,
                    worktree=worktree,
                    spec_rel=plan.spec_rel,
                    run_id=run_id,
                    policy=self.policy,
                    review_deps=self._deps(scratch, AgentRole.REVIEWER),
                )
                if not pipeline.accepted:
                    reason = f"{pipeline.blocked_reason}: {pipeline.evidence[:160]}"
                else:
                    holdout: HoldoutSource = self.holdout
                    suite = await FullSuitePhase(
                        self._deps(scratch, AgentRole.BUILDER),
                        task_id=task_id,
                        worktree=worktree,
                        spec_rel=plan.spec_rel,
                        run_id=run_id,
                        holdout=holdout,
                        issue_number=item.issue_number,
                        build_repairs=pipeline.build.repairs,
                    ).run()
                    if not suite.passed:
                        reason = f"{suite.blocked_reason}: {suite.evidence[:160]}"
            scratch.finish_run(run_id, RunStatus.SUCCESS if reason is None else RunStatus.FAIL)
            cost = float(
                scratch.db.scalar(
                    "SELECT COALESCE(SUM(cost_usd), 0) FROM phases WHERE run_id = ?", (run_id,)
                )
                or 0.0
            )
        except FactoryError as exc:
            reason = f"{exc.code}: {exc}"[:300]
            cost = 0.0
        finally:
            remove_worktree(self.repo_clone, worktree.path)
            run_git(["branch", "-D", constants.EVAL_BRANCH], cwd=self.repo_clone, check=False)
        return ItemResult(item, reason is None, cost, int(time.monotonic() - started), reason)

    # --- the run ----------------------------------------------------------------------------------

    def items_of(self, set_id: str) -> list[EvalItem]:
        rows = self.writer.eval_items(set_id)
        return [
            EvalItem(
                id=str(r["id"]),
                issue_number=int(r["issue_number"]),
                eval_class=str(r["class"]),
                title=f"issue {r['issue_number']}",
                merged_pr=int(r["merged_pr"]) if r["merged_pr"] is not None else None,
            )
            for r in rows
        ]

    async def run(
        self,
        set_id: str,
        *,
        trigger: Trigger,
        trigger_ref: str | None = None,
        baseline_run_id: str | None = None,
        titles: Mapping[int, str] | None = None,
        on_started: Callable[[str], None] | None = None,
    ) -> EvalReport:
        items = self.items_of(set_id)
        if titles:
            items = [
                EvalItem(
                    i.id,
                    i.issue_number,
                    i.eval_class,
                    titles.get(i.issue_number, i.title),
                    i.merged_pr,
                )
                for i in items
            ]
        if len(items) < constants.EVAL_MIN_ITEMS:
            raise EvalError(
                f"eval set {set_id} has {len(items)} items; the PRD requires at least"
                f" {constants.EVAL_MIN_ITEMS}"
            )
        baseline = (
            self.writer.get_eval_run(baseline_run_id)
            if baseline_run_id is not None
            else self.writer.latest_complete_eval_run(set_id)
        )
        if baseline_run_id is not None and baseline is None:
            raise EvalError(f"baseline run {baseline_run_id} does not exist")
        run_id = self.writer.start_eval_run(
            set_id=set_id,
            factory_version=self.factory_version,
            config_sha=self.config.config_sha,
            trigger=trigger,
            trigger_ref=trigger_ref,
            baseline_run_id=None if baseline is None else str(baseline["id"]),
        )
        if on_started is not None:
            on_started(run_id)
        run_dir = self.settings.data_dir / "eval" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        db, scratch, cap_usd = self._scratch(run_dir, len(items))
        results: list[ItemResult] = []
        try:
            for item in items:
                result = await self._replay(scratch, run_dir, item)
                results.append(result)
                self.writer.record_eval_result(
                    run_id,
                    item_id=item.id,
                    passed=result.passed,
                    cost_usd=result.cost_usd,
                    duration_s=result.duration_s,
                    failure_reason=result.failure_reason,
                )
        except BaseException:
            self.writer.finish_eval_run(run_id, status="interrupted")
            raise
        finally:
            db.close()
        overall, per_class = pass_rates(results)
        baseline_rate = (
            float(baseline["pass_rate"])
            if baseline is not None and baseline["pass_rate"] is not None
            else None
        )
        delta = delta_points(overall, baseline_rate)
        blocked = delta is not None and delta < -constants.EVAL_BLOCK_DROP_POINTS
        set_row = self.writer.eval_set(set_id)
        reference = (
            float(set_row["reference_pass_rate"])
            if set_row is not None and set_row["reference_pass_rate"] is not None
            else None
        )
        gap = delta_points(overall, reference)
        self.writer.finish_eval_run(
            run_id,
            status="complete",
            pass_rate=overall,
            per_class_json=json.dumps(per_class, sort_keys=True),
            delta_points=delta,
            blocked=blocked,
        )
        self.writer.write_event(
            EventKind.LOG,
            "orchestrator",
            payload={
                "eval_run": run_id,
                "set_id": set_id,
                "trigger": trigger,
                "pass_rate": overall,
                "per_class": per_class,
                "delta_points": delta,
                "blocked": blocked,
                "reference_pass_rate": reference,
                "gap_points": gap,
                "items": len(items),
                "cap_usd": cap_usd,
                "spent_usd": round(sum(r.cost_usd for r in results), 4),
            },
        )
        return EvalReport(
            run_id,
            set_id,
            overall,
            per_class,
            None if baseline is None else str(baseline["id"]),
            baseline_rate,
            delta,
            blocked,
            tuple(results),
            reference_pass_rate=reference,
            gap_points=gap,
        )
