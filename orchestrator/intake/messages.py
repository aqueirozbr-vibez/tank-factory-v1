"""Issue comments: named fields in, rendered template out (architecture.md · Templates).

Each function names the fields its template takes; the templates live in
``orchestrator/templates/comments/`` and refuse anything else.
"""

from collections.abc import Mapping, Sequence

from orchestrator import constants
from orchestrator.models.enums import BlockedReason
from orchestrator.templates import render_comment

# PRD §Capability 5 · Resume from blocked: what a person does next, per reason.
NEXT_STEP: dict[str, str] = {
    "open_questions": (
        "Answer the questions in this issue, then remove `{needs_human}`; the planner runs again"
        " with the answers."
    ),
    "missing_holdout": (
        "Add the holdout tests the spec names under `tests/acceptance/` on the holdout branch,"
        " then remove `{needs_human}`."
    ),
    "spec_gate": (
        "The planner could not produce a spec that passes the gate. Clarify the issue, then"
        " remove `{needs_human}` to plan again."
    ),
    "human_decision_needed": (
        "A person with write access decides: edit the spec on the task branch or fix the"
        " findings, then remove `{needs_human}`."
    ),
    "revision_cap": (
        "The reviewer asked for a third revision. Fix the findings on the branch or close the"
        " issue, then remove `{needs_human}`."
    ),
    "repair_cap": (
        "Removing `{needs_human}` starts a new attempt on the same branch with fresh caps."
    ),
    "full_suite": (
        "Removing `{needs_human}` starts a new attempt on the same branch with fresh caps."
    ),
    "timeout": "Removing `{needs_human}` starts a new attempt on the same branch with fresh caps.",
    "budget": "This task resumes only after a budget raise (`{budget}`).",
    "protected_path": (
        "This block never resumes on its own: close the issue, or change what caused it and"
        " re-add `{ready}` to start a new attempt."
    ),
    "dependency_change": (
        "A dependency change needs a person: install it in the setup step, then re-add"
        " `{ready}` to start a new attempt; this block never resumes on its own."
    ),
}


def _who(login: str | None) -> str:
    return f"@{login}" if login else "the actor"


def refused_actor(login: str | None, permission: str) -> str:
    return render_comment(
        "refused_actor", who=_who(login), permission=permission, label_ready=constants.LABEL_READY
    )


def queued(ahead: int, concurrency: int) -> str:
    return render_comment(
        "queued", ahead=ahead, noun="task" if ahead == 1 else "tasks", concurrency=concurrency
    )


CANCEL_CAUSE_LABEL = f"`{constants.LABEL_READY}` was removed"
CANCEL_CAUSE_KILL_SWITCH = "the factory was stopped (`FACTORY_ENABLED=0`)"


def cancelled(last_phase: str | None, *, cause: str | None = None) -> str:
    phase = f"after phase `{last_phase}`" if last_phase else "before any phase completed"
    return render_comment(
        "cancelled",
        phase=phase,
        cause=cause or CANCEL_CAUSE_LABEL,
        label_ready=constants.LABEL_READY,
    )


def open_questions(questions: Sequence[str], language: str) -> str:
    return render_comment(
        "open_questions",
        questions=list(questions),
        language=language,
        label_needs_human=constants.LABEL_NEEDS_HUMAN,
    )


def missing_holdout(test_ids: Sequence[str]) -> str:
    return render_comment(
        "missing_holdout", test_ids=list(test_ids), label_needs_human=constants.LABEL_NEEDS_HUMAN
    )


def plan_review(
    *,
    risk_class: str,
    equation: str,
    conclusion: str,
    spec_rel: str,
    spec_text: str,
    governed_path: str | None,
    owners: Sequence[str],
) -> str:
    if governed_path is not None:
        named = ", ".join(owners) if owners else "nobody is listed in CODEOWNERS"
        who = f"a CODEOWNER of `{governed_path}` ({named})"
    else:
        who = "a user with write access"
    return render_comment(
        "plan_review",
        risk_class=risk_class,
        equation=equation,
        conclusion=conclusion,
        who=who,
        spec_rel=spec_rel,
        spec_text=spec_text,
        label_plan_approved=constants.LABEL_PLAN_APPROVED,
    )


def approval_ignored(
    login: str | None,
    kind: str,
    *,
    permission: str | None = None,
    governed_path: str | None = None,
    owners: Sequence[str] = (),
) -> str:
    who = _who(login)
    if kind == "ignored_stale":
        why = "it was added before the current spec was committed; approvals bind to the spec sha"
    elif kind == "ignored_not_owner":
        named = ", ".join(owners) if owners else "nobody is listed in CODEOWNERS"
        why = (
            f"this task is `risk:governed` and {who} is not a CODEOWNER of"
            f" `{governed_path}` ({named})"
        )
    else:
        why = f"{who} has `{permission}` and plan approval needs write access"
    return render_comment(
        "approval_ignored", who=who, why=why, label_plan_approved=constants.LABEL_PLAN_APPROVED
    )


def review_findings(payload: Mapping[str, object], prior: Sequence[str]) -> str:
    """The needs-human comment after the review loop stops (PRD Capability 4)."""
    reasons = payload.get("human_reasons")
    why = ""
    if isinstance(reasons, list) and reasons:
        why = ", ".join(str(r) for r in reasons)
        if "third_revision" in reasons:
            why += f" (a third `revision_needed` after {constants.REVISION_CAP} revisions)"
    strongest = payload.get("strongest")
    findings = payload.get("findings")
    dismissed = payload.get("dismissed")
    return render_comment(
        "review_findings",
        verdict=str(payload.get("verdict")),
        conformance=payload.get("conformance"),
        why=why,
        strongest=strongest if isinstance(strongest, dict) else None,
        findings=[f for f in findings if isinstance(f, dict)] if isinstance(findings, list) else [],
        dismissed=[d for d in dismissed if isinstance(d, dict)]
        if isinstance(dismissed, list)
        else [],
        prior=list(prior),
        label_needs_human=constants.LABEL_NEEDS_HUMAN,
    )


def blocked(
    reason: BlockedReason | str,
    *,
    phase: str | None,
    summary: str,
    command: str | None = None,
    exit_code: int | None = None,
    output_tail: str | None = None,
) -> str:
    """The Blocked-Comment: the reason, the evidence and what a person does next (PRD Cap. 3, 5)."""
    key = str(reason)
    next_step = NEXT_STEP.get(key, "A person with write access decides; remove `{needs_human}`.")
    tail = output_tail[-constants.OUTPUT_TAIL_CHARS :] if output_tail else None
    return render_comment(
        "blocked",
        reason=key,
        phase=phase,
        summary=summary,
        command=command,
        exit_code=exit_code,
        output_tail=tail,
        next_step=next_step.format(
            needs_human=constants.LABEL_NEEDS_HUMAN,
            budget=constants.LABEL_BUDGET,
            ready=constants.LABEL_READY,
        ),
    )


def budget_stop(
    *, task_spent: float, task_budget: float, phase_cap: float, daily_spent: float, daily_cap: float
) -> str:
    return render_comment(
        "budget",
        task_spent=f"{task_spent:.2f}",
        task_budget=f"{task_budget:.2f}",
        phase_cap=f"{phase_cap:.2f}",
        daily_spent=f"{daily_spent:.2f}",
        daily_cap=f"{daily_cap:.2f}",
        raise_factor=f"{constants.BUDGET_RAISE_MAX_FACTOR:g}",
        label_budget=constants.LABEL_BUDGET,
        label_needs_human=constants.LABEL_NEEDS_HUMAN,
    )


def resume_refused(login: str | None, permission: str) -> str:
    return render_comment(
        "resume_refused",
        who=_who(login),
        permission=permission,
        label_needs_human=constants.LABEL_NEEDS_HUMAN,
    )
