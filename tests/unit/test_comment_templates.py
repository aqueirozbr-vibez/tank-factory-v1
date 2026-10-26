"""Unit 29: every comment renders from named fields; snapshots under fixtures/comments."""

from collections.abc import Callable
from pathlib import Path

import pytest
from jinja2 import UndefinedError

from orchestrator.instruments.risk import TIMES
from orchestrator.intake import messages
from orchestrator.models.enums import BlockedReason
from orchestrator.templates import comment_templates, render_comment, template_fields

SNAPSHOTS = Path(__file__).resolve().parents[2] / "fixtures" / "comments"
SPEC_SAMPLE = "# 418 — Invoice due date\n\nrisk: risk:high\n\n## Outcome\n\nAsk @rsouza later.\n"
STRONGEST = {"requirement_id": "AC2", "equation": f"70 {TIMES} 1 {TIMES} 1 {TIMES} 1 = 70"}
FINDINGS = [
    {
        "requirement_id": "AC2",
        "severity": "Major",
        "location": "src/billing/invoice.py:14",
        "problem": "Zero terms does not return issued_at (ask @mlima).",
        "score": 70.0,
    },
    {
        "requirement_id": "style",
        "severity": "Minor",
        "location": None,
        "problem": "Duplicated date arithmetic.",
        "score": 6.0,
    },
]
DISMISSED = [{"requirement_id": "performance", "dismissed_by": "Caching of invoice queries."}]

SAMPLES: dict[str, Callable[[], str]] = {
    "refused_actor": lambda: messages.refused_actor("reader", "read"),
    "resume_refused": lambda: messages.resume_refused("reader", "read"),
    "queued": lambda: messages.queued(2, 3),
    "cancelled": lambda: messages.cancelled("build"),
    "open_questions": lambda: messages.open_questions(
        ["Which timezone?", "Are terms calendar days?"], "en"
    ),
    "open_questions_pt": lambda: messages.open_questions(["Qual fuso horário?"], "pt-BR"),
    "missing_holdout": lambda: messages.missing_holdout(
        ["tests/acceptance/test_invoice_due_date_holdout.py::test_zero_terms"]
    ),
    "plan_review": lambda: messages.plan_review(
        risk_class="risk:governed",
        equation=f"100 {TIMES} 1 {TIMES} 1 {TIMES} 1 = 100",
        conclusion="tier A path: CODEOWNER plan approval before build.",
        spec_rel="specs/418-invoice-due-date.md",
        spec_text=SPEC_SAMPLE,
        governed_path="src/billing/invoice.py",
        owners=("@mlima",),
    ),
    "approval_ignored": lambda: messages.approval_ignored(
        "rsouza",
        "ignored_not_owner",
        permission="write",
        governed_path="src/billing/invoice.py",
        owners=("@mlima",),
    ),
    "review_findings": lambda: messages.review_findings(
        {
            "verdict": "human_decision_needed",
            "conformance": 30.0,
            "human_reasons": ["third_revision"],
            "strongest": STRONGEST,
            "findings": FINDINGS,
            "dismissed": DISMISSED,
        },
        ["revision_needed", "revision_needed"],
    ),
    "blocked": lambda: messages.blocked(
        BlockedReason.REPAIR_CAP,
        phase="code_gates",
        summary="Gate focused_tests failed after 3 repairs.",
        command="uv run pytest -q tests/unit/test_invoice_due_date.py",
        exit_code=1,
        output_tail="FAILED tests/unit/test_invoice_due_date.py::test_zero_terms - assert 1 == 0\n",
    ),
    "budget": lambda: messages.budget_stop(
        task_spent=5.4, task_budget=6.0, phase_cap=1.5, daily_spent=31.2, daily_cap=54.0
    ),
}


def write_snapshots() -> None:
    """Regenerate the snapshots after a deliberate wording change."""
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    for name, build in SAMPLES.items():
        (SNAPSHOTS / f"{name}.md").write_text(build() + "\n", encoding="utf-8", newline="\n")


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_snapshot_matches(name: str) -> None:
    snapshot = SNAPSHOTS / f"{name}.md"
    assert snapshot.is_file(), f"missing {snapshot}; run write_snapshots() after a wording change"
    assert SAMPLES[name]() == snapshot.read_text(encoding="utf-8").rstrip("\n")


def test_template_set_matches_the_renderers() -> None:
    expected = {
        "approval_ignored",
        "blocked",
        "budget",
        "cancelled",
        "missing_holdout",
        "open_questions",
        "plan_review",
        "queued",
        "refused_actor",
        "resume_refused",
        "review_findings",
    }
    assert set(comment_templates()) == expected
    assert template_fields("queued") == {"ahead", "noun", "concurrency"}


def test_unknown_and_missing_fields_are_refused() -> None:
    with pytest.raises(ValueError, match="declares no field \\['transcript'\\]"):
        render_comment("queued", ahead=1, noun="task", concurrency=3, transcript="agent text")
    with pytest.raises(UndefinedError):
        render_comment("queued", ahead=1, noun="task")


def test_mentions_in_agent_text_are_neutralised_but_owner_mentions_stay() -> None:
    plan = SAMPLES["plan_review"]()
    assert "`@rsouza`" in plan and "(@mlima)" in plan
    findings = SAMPLES["review_findings"]()
    assert "`@mlima`" in findings and "ask @mlima" not in findings
    assert "Prior verdicts: revision_needed, revision_needed." in findings
    assert '"Caching of invoice queries."' in findings


def test_blocked_next_steps_follow_the_resume_rules() -> None:
    never = messages.blocked(BlockedReason.PROTECTED_PATH, phase="code_gates", summary="x")
    assert "never resumes" in never and "factory:ready" in never and "Evidence" not in never
    fresh = messages.blocked(BlockedReason.TIMEOUT, phase="build", summary="x")
    assert "fresh caps" in fresh and "factory:needs-human" in fresh
    budget = messages.blocked(BlockedReason.BUDGET, phase=None, summary="x")
    assert "factory:budget" in budget and "in phase" not in budget
    repair = SAMPLES["blocked"]()
    assert "exited with code 1" in repair and "```text" in repair and "assert 1 == 0" in repair
    stop = SAMPLES["budget"]()
    assert "$5.40 of its $6.00" in stop and "2x, with a reason" in stop and "factory:budget" in stop
