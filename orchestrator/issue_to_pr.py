"""The state machine: issue → spec → build → review → PR (PRD §Architecture).

Phase 1 slice: ``build → code_gates → commit`` on a hand-written spec.
Phase 2 adds the review and the revision loop. Caps live here and in
``constants.py``, never in prompts (architecture.md · Invariant 15).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from orchestrator import constants
from orchestrator.errors import TraceError
from orchestrator.gates.spec import parse_spec
from orchestrator.instruments.verdict import ReviewPolicy, VerdictScore, score_review
from orchestrator.intake import messages
from orchestrator.intake.ports import GitHubPort
from orchestrator.models.enums import BlockedReason, EventKind, RunStatus, TaskStatus
from orchestrator.models.envelopes import Finding, VerifyOutput
from orchestrator.phases.build import BuildDeps, BuildOutcome, BuildPhase
from orchestrator.phases.deliver import DeliverOutcome, DeliverPhase, DeliveryPort
from orchestrator.phases.review import ReviewOutcome, ReviewPhase
from orchestrator.workspace.worktree import Worktree, diff_against_base


async def run_build_slice(
    deps: BuildDeps,
    *,
    task_id: str,
    worktree: Worktree,
    spec_rel: str,
    attempt: int = 1,
) -> BuildOutcome:
    """Run one build attempt for an admitted task whose spec already exists."""
    writer = deps.writer
    if writer.get_task(task_id) is None:
        raise TraceError(f"task {task_id} is not admitted")
    run_id = writer.start_run(
        task_id=task_id,
        attempt=attempt,
        factory_version=deps.factory_version,
        config_sha=deps.config.config_sha,
    )
    writer.set_task_attempt(task_id, attempt)
    writer.set_task_status(task_id, TaskStatus.RUNNING)
    phase = BuildPhase(deps, task_id=task_id, worktree=worktree, spec_rel=spec_rel, run_id=run_id)
    outcome = await phase.run()
    writer.finish_run(run_id, RunStatus.SUCCESS if outcome.passed else RunStatus.FAIL)
    return outcome


@dataclass(frozen=True)
class PipelineOutcome:
    status: Literal["accepted", "blocked"]
    run_id: str
    build: BuildOutcome
    reviews: tuple[ReviewOutcome, ...]
    scores: tuple[VerdictScore, ...]
    revisions: int
    blocked_reason: BlockedReason | None
    evidence: str
    notified: bool = False

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"

    @property
    def verdict(self) -> VerdictScore | None:
        return self.scores[-1] if self.scores else None


def missing_ac_findings(missing_ac: Sequence[str]) -> list[Finding]:
    """Orchestrator-authored notes for criteria the reviewer gave no verdict on."""
    return [
        Finding(
            requirement_id=ac,
            met=False,
            severity="Unverifiable",
            problem=(
                "The reviewer recorded no verdict for this acceptance criterion"
                " (orchestrator note, not a reviewer finding)."
            ),
            correction=(
                "Make its evidence explicit: the focused test the Test plan names must"
                " exercise it and be easy to find."
            ),
        )
        for ac in missing_ac
    ]


async def _needs_human(
    github: GitHubPort | None, *, issue_number: int | None, task_id: str, body: str
) -> None:
    if github is None or issue_number is None:
        return
    await github.create_comment(issue_number, body, task_id=task_id)
    await github.add_labels(issue_number, [constants.LABEL_NEEDS_HUMAN], task_id=task_id)


async def build_and_review(
    deps: BuildDeps,
    *,
    task_id: str,
    worktree: Worktree,
    spec_rel: str,
    run_id: str,
    github: GitHubPort | None = None,
    issue_number: int | None = None,
    policy: ReviewPolicy | None = None,
    review_deps: BuildDeps | None = None,
    initial_verify: VerifyOutput | None = None,
    repairs_used: int = 0,
    prior_build: BuildOutcome | None = None,
) -> PipelineOutcome:
    """build → review → (revision → review)* inside an existing run (PRD §Capability 3, 4)."""
    writer = deps.writer
    policy = policy or ReviewPolicy.from_yaml(deps.config.review_policy_yaml)
    reviews: list[ReviewOutcome] = []
    scores: list[VerdictScore] = []
    revisions = 0

    def blocked(
        build: BuildOutcome, reason: BlockedReason | None, evidence: str, *, notified: bool = False
    ) -> PipelineOutcome:
        return PipelineOutcome(
            "blocked",
            run_id,
            build,
            tuple(reviews),
            tuple(scores),
            revisions,
            reason,
            evidence,
            notified,
        )

    build = (
        prior_build
        or await BuildPhase(
            deps,
            task_id=task_id,
            worktree=worktree,
            spec_rel=spec_rel,
            run_id=run_id,
            initial_verify=initial_verify,
            repairs_used=repairs_used,
        ).run()
    )
    if not build.passed:
        return blocked(build, build.blocked_reason, build.evidence)
    spec_path = worktree.path.joinpath(*spec_rel.split("/"))
    spec = parse_spec(spec_path.read_text(encoding="utf-8"))

    while True:
        review = await ReviewPhase(
            review_deps or deps,
            task_id=task_id,
            worktree=worktree,
            spec_rel=spec_rel,
            run_id=run_id,
            attempt=revisions + 1,
        ).run()
        reviews.append(review)
        if not review.reviewed or review.envelope is None:
            return blocked(build, review.blocked_reason, review.evidence)
        score = score_review(
            policy,
            review.envelope,
            spec=spec,
            diff=diff_against_base(worktree.path, worktree.base_sha),
            missing_ac=review.missing_ac,
            revisions_so_far=revisions,
        )
        scores.append(score)
        writer.write_event(
            EventKind.LOG,
            "orchestrator",
            task_id=task_id,
            run_id=run_id,
            phase_id=review.phase_id,
            payload={"verdict_policy": score.payload()},
        )
        if score.accepted:
            return PipelineOutcome(
                "accepted", run_id, build, tuple(reviews), tuple(scores), revisions, None, ""
            )
        if score.verdict == "human_decision_needed":
            only_cap = score.human_reasons == ("third_revision",)
            reason = BlockedReason.REVISION_CAP if only_cap else BlockedReason.HUMAN_DECISION_NEEDED
            prior = [s.verdict for s in scores[:-1]]
            body = messages.review_findings(score.payload(), prior)
            await _needs_human(github, issue_number=issue_number, task_id=task_id, body=body)
            writer.set_task_status(task_id, TaskStatus.BLOCKED, blocked_reason=reason)
            writer.write_event(
                EventKind.ERROR,
                "orchestrator",
                task_id=task_id,
                run_id=run_id,
                phase_id=review.phase_id,
                payload={
                    "blocked_reason": str(reason),
                    "evidence": body[-constants.OUTPUT_TAIL_CHARS :],
                    "human_reasons": list(score.human_reasons),
                    "prior_verdicts": prior,
                },
            )
            return blocked(build, reason, body, notified=github is not None)
        revisions += 1
        findings = [*score.actionable_findings(), *missing_ac_findings(score.missing_ac)]
        build = await BuildPhase(
            deps,
            task_id=task_id,
            worktree=worktree,
            spec_rel=spec_rel,
            run_id=run_id,
            reviewer_findings=findings,
        ).run()
        if not build.passed:
            return blocked(build, build.blocked_reason, build.evidence)


async def run_build_and_review(
    deps: BuildDeps,
    *,
    task_id: str,
    worktree: Worktree,
    spec_rel: str,
    github: GitHubPort | None = None,
    issue_number: int | None = None,
    attempt: int = 1,
    policy: ReviewPolicy | None = None,
) -> PipelineOutcome:
    """``build_and_review`` in a run of its own (the unit 28 entry point)."""
    writer = deps.writer
    if writer.get_task(task_id) is None:
        raise TraceError(f"task {task_id} is not admitted")
    run_id = writer.start_run(
        task_id=task_id,
        attempt=attempt,
        factory_version=deps.factory_version,
        config_sha=deps.config.config_sha,
    )
    writer.set_task_attempt(task_id, attempt)
    writer.set_task_status(task_id, TaskStatus.RUNNING)
    outcome = await build_and_review(
        deps,
        task_id=task_id,
        worktree=worktree,
        spec_rel=spec_rel,
        run_id=run_id,
        github=github,
        issue_number=issue_number,
        policy=policy,
    )
    writer.finish_run(run_id, RunStatus.SUCCESS if outcome.accepted else RunStatus.FAIL)
    return outcome


async def deliver(
    deps: BuildDeps,
    *,
    task_id: str,
    worktree: Worktree,
    spec_rel: str,
    outcome: PipelineOutcome,
    github: DeliveryPort,
    base_branch: str,
    token: str | None = None,
    owners: Sequence[str] = (),
) -> DeliverOutcome:
    """Push and open the one PR for an accepted task (PRD §Capability 5; unit 30)."""
    if not outcome.accepted:
        raise TraceError(f"task {task_id} was not accepted; nothing to deliver")
    phase = DeliverPhase(
        deps,
        task_id=task_id,
        worktree=worktree,
        spec_rel=spec_rel,
        run_id=outcome.run_id,
        github=github,
        base_branch=base_branch,
        verdict=outcome.verdict,
        token=token,
        owners=owners,
    )
    return await phase.run()
