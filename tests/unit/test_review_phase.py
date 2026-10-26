"""Unit 27: the review phase on a real green build with the fake harness."""

from dataclasses import replace
from pathlib import Path

import pytest

from orchestrator.errors import ConfigError, PromptError
from orchestrator.harness.fake import FakeScript, FakeUsage
from orchestrator.issue_to_pr import run_build_slice
from orchestrator.models.config import FactoryConfig
from orchestrator.models.enums import AgentRole, BlockedReason
from orchestrator.phases.review import ReviewPhase, ac_ids_of, render_gate_evidence
from orchestrator.prompt_builder import build_reviewer_prompt, load_guide
from orchestrator.trace import TraceWriter, verify_chain
from orchestrator.workspace.worktree import Worktree, commit_all
from tests.unit.test_build_phase import (
    GREEN,
    SLEEPY,
    SPEC_REL,
    TASK_ID,
    deps_for,
    gates,
    make_config,
    phases,
    worktree_for,
)

REVIEW_USAGE = FakeUsage(input_tokens=15230, output_tokens=1480, cost_usd=0.2)


def finding(requirement: str, *, met: bool = True, severity: str = "Nit") -> dict[str, object]:
    return {
        "requirement_id": requirement,
        "met": met,
        "severity": severity,
        "file": "src/billing/invoice.py",
        "line": 12,
        "problem": "Met." if met else "The criterion is not implemented.",
        "impact": "",
        "correction": "" if met else "Implement it.",
        "evidence": "tests/unit/test_invoice_due_date.py::test_due_date_adds_terms",
    }


def review(
    session: str,
    *,
    approved: bool,
    verdict: str,
    findings: list[dict[str, object]],
    blocking: list[str] | None = None,
) -> FakeScript:
    return FakeScript(
        session_id=session,
        envelope={
            "approved": approved,
            "verdict": verdict,
            "findings": findings,
            "blocking": blocking or [],
        },
        usage=REVIEW_USAGE,
    )


ACCEPTED = review(
    "rev-ok", approved=True, verdict="accepted", findings=[finding("AC1"), finding("AC2")]
)
INCONSISTENT = review(
    "rev-bad",
    approved=True,
    verdict="accepted",
    findings=[finding("AC1", met=False, severity="Blocker"), finding("AC2")],
    blocking=["AC1"],
)
PARTIAL = review("rev-part", approved=True, verdict="accepted", findings=[finding("AC1")])
EMPTY_REVISION = review("rev-empty", approved=False, verdict="revision_needed", findings=[])
REVISION = review(
    "rev-fix",
    approved=False,
    verdict="revision_needed",
    findings=[finding("AC2", met=False, severity="Major")],
    blocking=["AC2"],
)
BAD_JSON = FakeScript(session_id="rev-json", envelope_text="not json", usage=REVIEW_USAGE)


async def build_then_review(
    trace_writer: TraceWriter,
    product: Path,
    tmp_path: Path,
    reviewer_scripts: list[FakeScript],
    *,
    config: FactoryConfig | None = None,
) -> tuple[ReviewPhase, Worktree, str]:
    deps, _, _ = deps_for(trace_writer, [GREEN, *reviewer_scripts], config=config)
    wt = worktree_for(product, tmp_path)
    built = await run_build_slice(deps, task_id=TASK_ID, worktree=wt, spec_rel=SPEC_REL)
    assert built.passed
    phase = ReviewPhase(deps, task_id=TASK_ID, worktree=wt, spec_rel=SPEC_REL, run_id=built.run_id)
    return phase, wt, built.run_id


async def test_green_review_records_its_own_phase_and_inputs(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    phase, _, run_id = await build_then_review(trace_writer, product, tmp_path, [ACCEPTED])
    outcome = await phase.run()
    assert outcome.reviewed and outcome.verdict == "accepted" and outcome.missing_ac == ()
    assert outcome.reviewer_calls == 1 and outcome.reprompts == 0
    assert outcome.cost_usd == pytest.approx(0.2) and outcome.session_id == "rev-ok"
    assert phases(trace_writer, run_id)[-1] == ("review", 1, "pass")
    assert gates(trace_writer, run_id)[-2:] == [("envelope_schema", 1), ("verdict_consistent", 1)]
    row = trace_writer.get_phase(outcome.phase_id or "")
    assert row is not None and row["cost_usd"] == pytest.approx(0.2)
    assert (
        row["tokens_in"] == 15230
        and row["owner"] == "reviewer"
        and row["model_id"] == "gpt-5-codex"
    )

    harness = phase.deps.harness
    request = harness.calls[-1]  # type: ignore[attr-defined]
    assert request.role is AgentRole.REVIEWER and request.resume_session_id is None
    assert request.tools == ("Read", "Glob", "Grep") and request.timeout_s == 2700
    assert request.system_prompt.startswith("# Reviewer")
    prompt = request.prompt
    assert "Blocker: 100" in prompt and "# 418 — Invoice due date" in prompt
    assert '"commit_message"' in prompt and "feat(billing): invoice due_date" in prompt
    assert (
        "- focused_tests (attempt 1): pass" in prompt
        and "diff --git a/src/billing/invoice.py" in prompt
    )
    assert "sess-green" not in prompt  # the builder's session never reaches the reviewer
    handoff = trace_writer.db.fetchone(
        "SELECT payload_json FROM events WHERE kind = 'handoff' AND phase_id = ?",
        (outcome.phase_id,),
    )
    assert handoff is not None and '"verdict":"accepted"' in handoff["payload_json"].replace(
        " ", ""
    )
    assert verify_chain(trace_writer.db).ok


async def test_inconsistent_envelope_is_reprompted_once_then_blocks(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    phase, _, run_id = await build_then_review(
        trace_writer, product, tmp_path, [INCONSISTENT, ACCEPTED]
    )
    outcome = await phase.run()
    assert outcome.reviewed and outcome.reviewer_calls == 2 and outcome.reprompts == 1
    second = phase.deps.harness.calls[-1]  # type: ignore[attr-defined]
    assert second.resume_session_id == "rev-bad" and "approved_with_blocking" in second.prompt
    assert gates(trace_writer, run_id)[-4:] == [
        ("envelope_schema", 1),
        ("verdict_consistent", 0),
        ("envelope_schema", 1),
        ("verdict_consistent", 1),
    ]


async def test_inconsistent_twice_blocks_for_a_human(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    phase, _, _ = await build_then_review(trace_writer, product, tmp_path, [INCONSISTENT])
    outcome = await phase.run()
    assert outcome.blocked_reason is BlockedReason.HUMAN_DECISION_NEEDED
    assert outcome.reviewer_calls == 2 and "approved_with_blocking" in outcome.evidence
    task = trace_writer.get_task(TASK_ID)
    assert task is not None and task["status"] == "blocked"
    assert task["blocked_reason"] == "human_decision_needed"


async def test_missing_ac_verdict_forces_revision_needed(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    phase, _, _ = await build_then_review(trace_writer, product, tmp_path, [PARTIAL])
    outcome = await phase.run()
    assert outcome.reviewed and outcome.reviewer_calls == 2
    assert outcome.envelope is not None and outcome.envelope.verdict == "accepted"
    assert outcome.verdict == "revision_needed" and outcome.missing_ac == ("AC2",)
    forced = trace_writer.db.fetchone(
        "SELECT payload_json FROM events WHERE kind = 'log'"
        " AND payload_json LIKE '%verdict_forced%'"
    )
    assert forced is not None and "AC2" in forced["payload_json"]
    second = phase.deps.harness.calls[-1]  # type: ignore[attr-defined]
    assert "AC2: no met verdict with evidence" in second.prompt


async def test_revision_without_findings_is_reprompted(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    phase, _, _ = await build_then_review(
        trace_writer, product, tmp_path, [EMPTY_REVISION, REVISION]
    )
    outcome = await phase.run()
    assert outcome.reviewed and outcome.verdict == "revision_needed" and outcome.reprompts == 1
    assert outcome.missing_ac == ("AC1",)  # reported, not a violation on a revision verdict
    assert outcome.envelope is not None and outcome.envelope.blocking == ["AC2"]


async def test_invalid_envelope_twice_blocks(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    phase, _, _ = await build_then_review(trace_writer, product, tmp_path, [BAD_JSON])
    outcome = await phase.run()
    assert outcome.blocked_reason is BlockedReason.HUMAN_DECISION_NEEDED
    assert outcome.reviewer_calls == 2 and "not JSON" in outcome.evidence


async def test_timeout_blocks(product: Path, tmp_path: Path, trace_writer: TraceWriter) -> None:
    cfg = make_config()
    reviewer = cfg.roster.reviewer.model_copy(update={"timeout_s": 1})
    cfg = cfg.model_copy(update={"roster": cfg.roster.model_copy(update={"reviewer": reviewer})})
    phase, _, _ = await build_then_review(trace_writer, product, tmp_path, [SLEEPY], config=cfg)
    outcome = await phase.run()
    assert outcome.blocked_reason is BlockedReason.TIMEOUT and outcome.reviewer_calls == 1


async def test_secret_in_the_diff_blocks_without_a_reviewer_call(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    phase, wt, _ = await build_then_review(trace_writer, product, tmp_path, [ACCEPTED])
    leaked = wt.path / "src" / "billing" / "settings.py"
    leaked.write_text("GITHUB_TOKEN = 'ghp_" + "a1" * 15 + "'\n", encoding="utf-8")
    commit_all(wt.path, "leak")
    outcome = await phase.run()
    assert outcome.blocked_reason is BlockedReason.HUMAN_DECISION_NEEDED
    assert "credential-shaped" in outcome.evidence and outcome.reviewer_calls == 0
    assert len(phase.deps.harness.calls) == 1  # type: ignore[attr-defined]


def test_same_vendor_roster_is_refused(trace_writer: TraceWriter, tmp_path: Path) -> None:
    cfg = make_config()
    reviewer = cfg.roster.reviewer.model_copy(update={"vendor": "anthropic"})
    cfg = cfg.model_copy(update={"roster": cfg.roster.model_copy(update={"reviewer": reviewer})})
    deps, _, _ = deps_for(trace_writer, [GREEN])  # the trace refuses such a config too
    same_vendor = replace(deps, config=cfg)
    wt = Worktree(repo=tmp_path, path=tmp_path, branch="agent/418", base_sha="0" * 40)
    with pytest.raises(ConfigError, match="share one vendor"):
        ReviewPhase(same_vendor, task_id=TASK_ID, worktree=wt, spec_rel=SPEC_REL, run_id="r")


def test_prompt_builder_and_helpers() -> None:
    guide = load_guide(AgentRole.REVIEWER)
    bundle = build_reviewer_prompt(
        guide,
        policy_text="severities:\n  Blocker: 100\n",
        spec_text="# Spec\n",
        builder_envelope_text='{"status": "success"}',
        gate_evidence="- lint (attempt 1): pass",
        diff="diff --git a/x b/x\n",
        correction="Gate verdict_consistent failed.",
    )
    assert bundle.sections == (
        "policy",
        "spec",
        "builder_envelope",
        "gate_evidence",
        "diff",
        "correction",
    )
    assert bundle.user_prompt.index("## Review policy") < bundle.user_prompt.index("## Spec")
    assert bundle.user_prompt.index("## Gate evidence") < bundle.user_prompt.index("## Diff")
    assert bundle.user_prompt.rstrip().endswith("Gate verdict_consistent failed.")
    with pytest.raises(PromptError, match="credential-shaped"):
        build_reviewer_prompt(
            guide,
            policy_text="",
            spec_text="",
            builder_envelope_text="",
            gate_evidence="",
            diff="+API_KEY=sk-" + "x" * 30,
        )
    with pytest.raises(PromptError, match="reviewer guide"):
        build_reviewer_prompt(
            load_guide(AgentRole.BUILDER),
            policy_text="",
            spec_text="",
            builder_envelope_text="",
            gate_evidence="",
            diff="",
        )
    assert ac_ids_of(
        "## Acceptance criteria\n\n1. WHEN a THE SYSTEM SHALL b.\n2. WHEN c THE SYSTEM SHALL d.\n"
    ) == ("AC1", "AC2")
    assert render_gate_evidence([]) == "No gate results recorded for this run."
