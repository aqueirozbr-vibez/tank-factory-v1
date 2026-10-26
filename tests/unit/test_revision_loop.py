"""Unit 28: build → review → revision loop on a real repository with the fake harness."""

from pathlib import Path

from orchestrator.harness.fake import FakeEffect, FakeScript
from orchestrator.issue_to_pr import run_build_and_review
from orchestrator.models.enums import BlockedReason
from orchestrator.trace import TraceWriter, verify_chain
from tests.unit.test_admission import FakeGitHub
from tests.unit.test_build_phase import (
    ENVELOPE,
    GREEN,
    SPEC_REL,
    SRC,
    TASK_ID,
    USAGE,
    deps_for,
    phases,
    worktree_for,
)
from tests.unit.test_review_phase import ACCEPTED, PARTIAL, REVIEW_USAGE, finding

INVOICE = "src/billing/invoice.py"
LAST = len(SRC.splitlines())  # the last line of the builder's file, present in the diff


def revised(number: int) -> FakeScript:
    return FakeScript(
        session_id=f"sess-rev{number}",
        effects=(FakeEffect(write=INVOICE, content=SRC + f"\n# revision {number}\n"),),
        envelope={
            **ENVELOPE,
            "changed_files": [INVOICE],
            "tests_added": [],
            "commit_message": f"fix(billing): address review round {number} (#418)",
        },
        usage=USAGE,
    )


def review_round(session: str, *, verdict: str, findings: list[dict[str, object]]) -> FakeScript:
    return FakeScript(
        session_id=session,
        envelope={
            "approved": verdict == "accepted",
            "verdict": verdict,
            "findings": findings,
            "blocking": [f["requirement_id"] for f in findings if f.get("severity") == "Blocker"],
        },
        usage=REVIEW_USAGE,
    )


UNMET_AC2 = {
    **finding("AC2", met=False, severity="Major"),
    "line": LAST,
    "problem": "Zero terms does not return issued_at.",
    "evidence": "tests/unit/test_invoice_due_date.py::test_zero_terms_is_same_day fails",
}
REVISION = review_round(
    "rev-needed", verdict="revision_needed", findings=[finding("AC1"), UNMET_AC2]
)
HUMAN = review_round(
    "rev-human",
    verdict="human_decision_needed",
    findings=[
        finding("AC1"),
        {
            **finding("Scope", met=False, severity="Major"),
            "line": LAST,
            "problem": "src/api/list.py changed outside Scope and Non-goals does not mention it.",
        },
    ],
)


async def run_pipeline(
    trace_writer: TraceWriter, product: Path, tmp_path: Path, scripts: list[FakeScript]
) -> tuple[object, FakeGitHub, object]:
    deps, harness, _ = deps_for(trace_writer, scripts)
    wt = worktree_for(product, tmp_path)
    github = FakeGitHub()
    outcome = await run_build_and_review(
        deps, task_id=TASK_ID, worktree=wt, spec_rel=SPEC_REL, github=github, issue_number=418
    )
    return outcome, github, harness


async def test_revision_then_accepted(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    outcome, github, harness = await run_pipeline(
        trace_writer, product, tmp_path, [GREEN, REVISION, revised(1), ACCEPTED]
    )
    assert outcome.accepted and outcome.revisions == 1  # type: ignore[attr-defined]
    assert [s.verdict for s in outcome.scores] == ["revision_needed", "accepted"]  # type: ignore[attr-defined]
    first = outcome.scores[0]  # type: ignore[attr-defined]
    assert (
        first.strongest is not None and first.strongest.score == 70.0 and first.conformance == 30.0
    )
    names = [name for name, _, _ in phases(trace_writer, outcome.run_id)]  # type: ignore[attr-defined]
    assert names == [
        "build",
        "code_gates",
        "commit",
        "review",
        "revision",
        "code_gates",
        "commit",
        "review",
    ]
    revision_request = harness.calls[2]  # type: ignore[attr-defined]
    assert "## Reviewer findings" in revision_request.prompt
    assert "Zero terms does not return issued_at." in revision_request.prompt
    assert revision_request.resume_session_id is None  # a revision is a fresh builder session
    assert github.calls == []
    run = trace_writer.get_run(outcome.run_id)  # type: ignore[attr-defined]
    assert run is not None and run["status"] == "success"
    policy_events = trace_writer.db.fetchall(
        "SELECT payload_json FROM events WHERE kind = 'log'"
        " AND payload_json LIKE '%verdict_policy%'"
    )
    assert len(policy_events) == 2 and "= 70" in policy_events[0]["payload_json"]
    assert verify_chain(trace_writer.db).ok


async def test_third_revision_needed_stops_for_a_human(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    scripts = [GREEN, REVISION, revised(1), REVISION, revised(2), REVISION]
    outcome, github, _ = await run_pipeline(trace_writer, product, tmp_path, scripts)
    assert outcome.blocked_reason is BlockedReason.REVISION_CAP and outcome.revisions == 2  # type: ignore[attr-defined]
    assert [s.verdict for s in outcome.scores] == [  # type: ignore[attr-defined]
        "revision_needed",
        "revision_needed",
        "human_decision_needed",
    ]
    assert outcome.scores[-1].human_reasons == ("third_revision",)  # type: ignore[attr-defined]
    comment = github.comments()[-1]
    assert (
        "third" in comment.lower()
        and "AC2" in comment
        and "revision_needed, revision_needed" in comment
    )
    assert ("add_labels", (418, ("factory:needs-human",))) in github.calls
    task = trace_writer.get_task(TASK_ID)
    assert task is not None and task["status"] == "blocked"
    assert task["blocked_reason"] == "revision_cap"
    run = trace_writer.get_run(outcome.run_id)  # type: ignore[attr-defined]
    assert run is not None and run["status"] == "fail"


async def test_reviewer_human_class_stops_at_once(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    outcome, github, _ = await run_pipeline(trace_writer, product, tmp_path, [GREEN, HUMAN])
    assert outcome.blocked_reason is BlockedReason.HUMAN_DECISION_NEEDED  # type: ignore[attr-defined]
    assert outcome.revisions == 0 and len(outcome.reviews) == 1  # type: ignore[attr-defined]
    comment = github.comments()[-1]
    assert "human_decision_needed" in comment and "src/api/list.py" in comment
    assert ("add_labels", (418, ("factory:needs-human",))) in github.calls


async def test_forced_revision_hands_the_builder_a_note_per_missing_criterion(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    outcome, _, harness = await run_pipeline(
        trace_writer, product, tmp_path, [GREEN, PARTIAL, PARTIAL, revised(1), ACCEPTED]
    )
    assert outcome.accepted and outcome.revisions == 1  # type: ignore[attr-defined]
    assert outcome.scores[0].verdict == "revision_needed"  # type: ignore[attr-defined]
    assert outcome.scores[0].missing_ac == ("AC2",)  # type: ignore[attr-defined]
    revision_request = harness.calls[3]  # type: ignore[attr-defined]
    assert "recorded no verdict for this acceptance criterion" in revision_request.prompt
    assert '"requirement_id": "AC2"' in revision_request.prompt


async def test_unchanged_revision_still_reaches_review(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    argued = FakeScript(
        session_id="sess-argue",
        envelope={**ENVELOPE, "changed_files": [], "tests_added": [], "summary": "no change"},
        usage=USAGE,
    )
    outcome, _, _ = await run_pipeline(
        trace_writer, product, tmp_path, [GREEN, REVISION, argued, ACCEPTED]
    )
    assert outcome.accepted and outcome.revisions == 1  # type: ignore[attr-defined]
    unchanged = trace_writer.db.fetchone(
        "SELECT payload_json FROM events WHERE kind = 'handoff' AND payload_json LIKE '%unchanged%'"
    )
    assert unchanged is not None
