"""Unit 28: the review verdict instrument; the PRD's worked examples are the fixtures."""

from pathlib import Path

import pytest
import yaml

from orchestrator.errors import ConfigError
from orchestrator.gates.spec import parse_spec
from orchestrator.instruments.verdict import (
    ReviewPolicy,
    diff_lines,
    non_goal_match,
    override_verdict,
    record_verdict_override,
    score_finding,
    score_review,
)
from orchestrator.models.enums import WorkspaceKind
from orchestrator.models.envelopes import Finding, ReviewOutput
from orchestrator.trace import TraceWriter
from tests.unit.test_build_phase import make_config
from tests.unit.test_spec_gate import SPEC_TEXT

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "fixtures" / "factory_config"
POLICY_YAML = (CONFIG_DIR / "review-policy.yaml").read_text(encoding="utf-8")
INVOICE = "src/billing/invoice.py"
DIFF = (
    "diff --git a/src/billing/invoice.py b/src/billing/invoice.py\n"
    "index 1111111..2222222 100644\n"
    "--- a/src/billing/invoice.py\n"
    "+++ b/src/billing/invoice.py\n"
    "@@ -1,2 +1,6 @@\n"
    " class Invoice:\n"
    "-    pass\n"
    "+    terms_days = 30\n"
    "+\n"
    "+    @property\n"
    "+    def due_date(self):\n"
    "+        return self.issued_at + timedelta(days=self.terms_days)\n"
    "diff --git a/tests/unit/test_invoice_due_date.py b/tests/unit/test_invoice_due_date.py\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/tests/unit/test_invoice_due_date.py\n"
    "@@ -0,0 +1,2 @@\n"
    "+def test_due_date_adds_terms():\n"
    "+    assert True\n"
    "\\ No newline at end of file\n"
)
SPEC = parse_spec(SPEC_TEXT)


@pytest.fixture(scope="module")
def policy() -> ReviewPolicy:
    return ReviewPolicy.from_yaml(POLICY_YAML)


def finding(
    requirement: str,
    *,
    severity: str,
    problem: str = "Behaviour diverges from the criterion.",
    file: str | None = INVOICE,
    line: int | None = 5,
    evidence: str = "",
    met: bool = False,
    impact: str = "",
) -> Finding:
    return Finding(
        requirement_id=requirement,
        met=met,
        severity=severity,
        file=file,
        line=line,
        problem=problem,
        impact=impact,
        evidence=evidence,
    )


def envelope(verdict: str, *findings: Finding) -> ReviewOutput:
    return ReviewOutput(
        approved=verdict == "accepted",
        verdict=verdict,
        findings=list(findings),
        blocking=[f.requirement_id for f in findings if f.severity == "Blocker"],
    )


def test_policy_loads_and_rejects_bad_yaml(policy: ReviewPolicy) -> None:
    assert ReviewPolicy.load(CONFIG_DIR) == policy
    assert policy.severities["Major"] == 70 and policy.thresholds.accept_conformance == 70
    with pytest.raises(ConfigError, match="invalid"):
        ReviewPolicy.from_yaml("version: [")
    raw = yaml.safe_load(POLICY_YAML)
    del raw["severities"]["Nit"]
    with pytest.raises(ConfigError, match="lacks severities"):
        ReviewPolicy.from_yaml(yaml.safe_dump(raw))
    with pytest.raises(ConfigError, match="missing"):
        ReviewPolicy.load(CONFIG_DIR / "nope")


def test_diff_lines_cover_context_and_added_lines() -> None:
    lines = diff_lines(DIFF)
    assert lines[INVOICE] == {1, 2, 3, 4, 5, 6}
    assert lines["tests/unit/test_invoice_due_date.py"] == {1, 2}
    assert diff_lines("") == {}


def test_prd_worked_example_one_major_on_ac3(policy: ReviewPolicy) -> None:
    major = finding("AC3", severity="Major", evidence=f"{INVOICE}:5")
    score = score_review(policy, envelope("revision_needed", major), spec=SPEC, diff=DIFF)
    assert score.strongest is not None and score.strongest.score == 56.0
    assert score.strongest.equation().endswith("= 56")
    assert score.conformance == 44.0 and score.verdict == "revision_needed"
    assert score.actionable_findings() == [major]


def test_prd_worked_example_two_minor_without_location(policy: ReviewPolicy) -> None:
    minor = finding(
        "style", severity="Minor", problem="Duplicated date arithmetic.", file=None, line=None
    )
    score = score_review(policy, envelope("accepted", minor), spec=SPEC, diff=DIFF)
    assert score.strongest is not None and score.strongest.score == 6.0
    assert (score.strongest.spec_link, score.strongest.evidence) == (0.5, 0.4)
    assert score.conformance == 94.0 and score.accepted
    assert score.strongest.location is None  # kept as advisory in the PR body


def test_prd_worked_example_three_blocker_then_human(policy: ReviewPolicy) -> None:
    blocker = finding(
        "AC1",
        severity="Blocker",
        problem="Due date is not computed.",
        evidence="tests/acceptance/test_invoice_due_date_holdout.py::test_due_date fails",
    )
    first = score_review(policy, envelope("revision_needed", blocker), spec=SPEC, diff=DIFF)
    assert first.blocking_score == 100.0 and first.conformance == 0.0
    assert first.verdict == "revision_needed" and first.human_reasons == ()
    third = score_review(
        policy, envelope("revision_needed", blocker), spec=SPEC, diff=DIFF, revisions_so_far=2
    )
    assert third.verdict == "human_decision_needed" and third.human_reasons == ("third_revision",)
    assert third.revisions_so_far == 2


def test_prd_worked_example_four_non_goals_dismissal(policy: ReviewPolicy) -> None:
    caching = finding(
        "performance",
        severity="Major",
        problem="No caching of invoice queries; every call recomputes the due date.",
        evidence=f"{INVOICE}:6",
    )
    assert non_goal_match(caching, SPEC) == "Caching of invoice queries."
    score = score_review(policy, envelope("accepted", caching), spec=SPEC, diff=DIFF)
    assert score.scores == () and len(score.dismissed) == 1
    assert score.dismissed[0].score == 0.0 and score.dismissed[0].scope == 0.0
    assert score.dismissed[0].equation().startswith("70 ")
    assert score.dismissed[0].dismissed_by == "Caching of invoice queries."
    assert score.dismissed[0].payload()["scope"] == 0.0
    assert score.payload()["accept_conformance"] == 70.0
    assert score.conformance == 100.0 and score.accepted
    cited = finding("Non-goals 2", severity="Minor", problem="The list endpoint lacks it.")
    assert non_goal_match(cited, SPEC) == "Exposing due_date on the list endpoint."
    ordinary = finding(
        "AC2", severity="Major", problem="Zero terms returns the wrong invoice date."
    )
    assert non_goal_match(ordinary, SPEC) is None  # "invoice" is everywhere in the spec


def test_prd_worked_example_five_override_is_recorded(
    policy: ReviewPolicy, trace_writer: TraceWriter
) -> None:
    minor = finding("AC2", severity="Minor", evidence=f"{INVOICE}:4")
    score = score_review(policy, envelope("accepted", minor), spec=SPEC, diff=DIFF)
    assert score.accepted and score.strongest is not None and score.strongest.score == 24.0
    overridden = override_verdict(score, "revision_needed", by="mlima", reason="Minor was a Major")
    assert overridden.verdict == "revision_needed" and overridden.overridden_by == "mlima"
    assert overridden.conformance == score.conformance  # the computed score stays
    cfg = make_config()
    trace_writer.load_config_version(**cfg.config_version_row())
    trace_writer.insert_task(
        task_id="acme/atlas-api#418",
        issue_number=418,
        title="t",
        branch="agent/418",
        workspace_kind=WorkspaceKind.WORKTREE,
        budget_usd=6.0,
        admitted_by="rsouza",
        config_sha=cfg.config_sha,
    )
    recorded = record_verdict_override(
        trace_writer,
        task_id="acme/atlas-api#418",
        score=score,
        verdict="revision_needed",
        by="mlima",
        reason="Minor was a Major",
        reviewer_model="gpt-5-codex",
    )
    assert recorded.override_reason == "Minor was a Major"
    row = trace_writer.db.fetchone("SELECT actor, payload_json FROM events WHERE kind = 'audit'")
    assert row is not None and row["actor"] == "mlima"
    assert "gpt-5-codex" in row["payload_json"] and '"policy_version": 1' in row[
        "payload_json"
    ].replace(":1", ": 1")


def test_unverifiable_location_security_blocker_and_factors(policy: ReviewPolicy) -> None:
    lines = diff_lines(DIFF)
    ghost = finding("AC1", severity="Major", line=40, evidence=f"{INVOICE}:40")
    scored = score_finding(policy, ghost, spec=SPEC, lines=lines)
    assert scored.tier == "Unverifiable" and scored.score == 0.0 and scored.in_diff is False
    assert "not in the diff" in scored.notes[0]

    constraint = finding("Constraint 1", severity="Minor", evidence="pytest -k due_date fails")
    scored = score_finding(policy, constraint, spec=SPEC, lines=lines)
    assert (scored.spec_link, scored.evidence, scored.score) == (0.9, 1.0, 27.0)
    scope_item = finding(INVOICE, severity="Nit", evidence=f"{INVOICE}:3")
    assert score_finding(policy, scope_item, spec=SPEC, lines=lines).spec_link == 1.0

    leak = finding("AC1", severity="Blocker", problem="A hard-coded API key is in the diff.")
    score = score_review(policy, envelope("revision_needed", leak), spec=SPEC, diff=DIFF)
    assert score.verdict == "human_decision_needed"
    assert score.human_reasons == ("security_blocker: AC1",)

    declared = score_review(
        policy,
        envelope("human_decision_needed", finding("Scope", severity="Major")),
        spec=SPEC,
        diff=DIFF,
    )
    assert declared.verdict == "human_decision_needed"
    assert declared.human_reasons == ("reviewer: human_decision_needed",)

    met_only = score_review(
        policy,
        envelope("accepted", finding("AC1", severity="Nit", met=True, evidence="t.py::a")),
        spec=SPEC,
        diff=DIFF,
    )
    assert met_only.scores == () and met_only.accepted and met_only.strongest is None
    forced = score_review(policy, envelope("accepted"), spec=SPEC, diff=DIFF, missing_ac=["AC2"])
    assert forced.verdict == "revision_needed" and forced.conformance == 100.0
