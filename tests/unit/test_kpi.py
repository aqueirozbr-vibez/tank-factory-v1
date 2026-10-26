"""Unit 48: the KPI materialiser against hand-computed fixtures; the audit sampler's strata."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from orchestrator.errors import TraceError
from orchestrator.models.enums import (
    BlockedReason,
    EventKind,
    PhaseKind,
    PhaseName,
    PhaseStatus,
    RunStatus,
    TaskStatus,
    WorkspaceKind,
)
from orchestrator.phases.deliver import MergeObserver
from orchestrator.trace import TraceWriter
from orchestrator.trace.kpi import (
    HEADLINE,
    KpiMaterialiser,
    audit_sampled,
    local_day,
    local_month,
    ratio,
)
from tests.unit.test_deliver import merged_event

NOW = datetime(2026, 10, 24, 15, 0, tzinfo=UTC)
LATE = datetime(2026, 10, 25, 2, 0, tzinfo=UTC)  # still the 24th in Sao Paulo


def admit(
    writer: TraceWriter, issue: int, *, risk: str | None = "risk:low", budget: float = 6.0
) -> str:
    task_id = f"acme/atlas-api#{issue}"
    writer.insert_task(
        task_id=task_id,
        issue_number=issue,
        title=f"task {issue}",
        branch=f"agent/{issue}",
        workspace_kind=WorkspaceKind.WORKTREE,
        budget_usd=budget,
        admitted_by="rsouza",
        config_sha="cfg1",
    )
    if risk is not None:
        writer.set_task_risk(task_id, risk=risk, score=10.0, governed_path=None)
    return task_id


def run_with(
    writer: TraceWriter,
    task_id: str,
    *,
    attempt: int = 1,
    cost: float = 0.0,
    code_gate_fails: int = 0,
    engineer: bool = False,
) -> str:
    run_id = writer.start_run(
        task_id=task_id, attempt=attempt, factory_version="0.1.0", config_sha="cfg1"
    )
    seq = 0

    def phase(name: PhaseName, kind: PhaseKind, status: PhaseStatus, usd: float = 0.0) -> None:
        nonlocal seq
        seq += 1
        phase_id = writer.start_phase(
            run_id=run_id, seq=seq, name=name, kind=kind, owner="x", timeout_s=60
        )
        writer.finish_phase(phase_id, status, cost_usd=usd)

    if engineer:
        phase(PhaseName.PLAN_APPROVAL, PhaseKind.ENGINEER, PhaseStatus.PASS)
    phase(PhaseName.BUILD, PhaseKind.AGENT, PhaseStatus.PASS, cost)
    for _ in range(code_gate_fails):
        phase(PhaseName.CODE_GATES, PhaseKind.CODE, PhaseStatus.FAIL)
    phase(PhaseName.CODE_GATES, PhaseKind.CODE, PhaseStatus.PASS)
    writer.finish_run(run_id, RunStatus.SUCCESS)
    return run_id


def open_pr(writer: TraceWriter, task_id: str, pr: int) -> None:
    writer.write_event(
        EventKind.HANDOFF,
        "orchestrator",
        task_id=task_id,
        payload={"pr": pr, "head_sha": "a" * 40, "created": True},
    )
    writer.set_task_pr(task_id, pr_number=pr, head_sha="a" * 40)
    writer.set_task_status(task_id, TaskStatus.PR_OPEN)


def test_ratio_and_sampler_rules() -> None:
    assert ratio(2, 3, "x").value == 66.67
    assert ratio(0, 0, "x").value is None
    assert audit_sampled("acme/atlas-api#1", "2026-10", "risk:high")
    assert audit_sampled("acme/atlas-api#1", "2026-10", "risk:governed")
    draws = [audit_sampled(f"acme/atlas-api#{n}", "2026-10", "risk:low") for n in range(4000)]
    share = sum(draws) / len(draws)
    assert 0.07 < share < 0.13, share  # Bernoulli 0.10 over 4,000 reproducible coins
    assert local_day(NOW, "America/Sao_Paulo") == "2026-10-24"
    assert local_day(LATE, "America/Sao_Paulo") == "2026-10-24"
    assert local_day(LATE, "UTC") == "2026-10-25"
    assert local_month(NOW, "UTC") == "2026-10"


@given(
    task=st.from_regex(r"[a-z]{1,8}/[a-z]{1,8}#[0-9]{1,5}", fullmatch=True),
    month=st.from_regex(r"20[0-9]{2}-(0[1-9]|1[0-2])", fullmatch=True),
    stratum=st.sampled_from(["risk:low", "risk:high", "risk:governed"]),
)
def test_sampler_is_deterministic_and_total_on_high_strata(
    task: str, month: str, stratum: str
) -> None:
    first = audit_sampled(task, month, stratum)
    assert first == audit_sampled(task, month, stratum)
    if stratum != "risk:low":
        assert first is True


async def test_metrics_match_hand_computed_fixtures(trace_writer: TraceWriter) -> None:
    w = trace_writer
    a = admit(w, 1)  # merged, low, cost 1.0
    b = admit(w, 2, risk="risk:high", budget=2.5)  # merged, high, runs 2.0 + 1.0: over budget
    c = admit(w, 3)  # pr_open after a plan-approval wait: attended
    d = admit(w, 4)  # blocked after two repairs
    admit(w, 5, risk=None)  # queued
    run_with(w, a, cost=1.0)
    run_with(w, b, attempt=1, cost=2.0)
    run_with(w, b, attempt=2, cost=1.0)
    run_with(w, c, engineer=True)
    run_with(w, d, code_gate_fails=2)
    open_pr(w, a, 431)
    open_pr(w, b, 432)
    open_pr(w, c, 433)
    w.set_task_status(d, TaskStatus.BLOCKED, blocked_reason=BlockedReason.REPAIR_CAP)
    w.insert_audit_decision(
        task_id=a, pr_number=431, month="2026-10", stratum="risk:low", sampled=True
    )  # the coin is fixed for the fixture: a is drawn

    kpis = KpiMaterialiser(w, clock=lambda: NOW)
    observer = MergeObserver(w, on_merge=kpis.on_merge)
    assert await observer.on_merged(merged_event(431)) is True
    assert await observer.on_merged(merged_event(432)) is True
    assert await observer.on_merged(merged_event(9, branch="feature/x")) is False  # a human PR
    queue = w.audit_queue()
    assert [(str(r["task_id"]), r["pr"], r["stratum"]) for r in queue] == [
        (a, 431, "risk:low"),
        (b, 432, "risk:high"),
    ]
    w.write_event(
        EventKind.LOG,
        "orchestrator",
        task_id=d,
        payload={"kind": "protected_path_attempt", "paths": ["CODEOWNERS"], "rolled_back": []},
    )
    w.write_event(EventKind.LOG, "orchestrator", task_id=d, payload={"stuck": True, "idle_s": 900})
    set_id = w.create_eval_set(name="pilot", repo="atlas-api", reference_pass_rate=80.0)
    run_id = w.start_eval_run(
        set_id=set_id,
        factory_version="0.1.0",
        config_sha="cfg1",
        trigger="manual",
        trigger_ref=None,
        baseline_run_id=None,
    )
    w.finish_eval_run(
        run_id, status="complete", pass_rate=75.0, per_class_json=json.dumps({"bug": 50.0})
    )
    w.decide_audit(task_id=a, month="2026-10", decision="disagree", reason="Minor", by="mlima")
    w.decide_audit(task_id=b, month="2026-10", decision="agree", reason="fine", by="mlima")

    metrics = {m.name: m for m in kpis.materialise(now=NOW)}
    assert all(name in metrics for name in HEADLINE)
    assert metrics["factory_merge_share"].value == 66.67  # 2 factory merges of 3 merged PRs
    assert (metrics["merge_rate"].numerator, metrics["merge_rate"].denominator) == (2, 3)
    assert metrics["unattended_rate"].value == 60.0  # a, b, d of 5 admitted; c waited on a human
    low, high = metrics["audit_disagreement_low"], metrics["audit_disagreement_high"]
    assert (low.value, low.denominator) == (100.0, 1)  # a disagreed, the only low draw
    assert (high.value, high.denominator) == (0.0, 1)
    assert metrics["audit_disagreement_governed"].value is None
    assert metrics["cost_per_merged_pr"].value == 2.0  # (1.0 + 3.0) / 2
    assert metrics["cost_per_merged_pr_median_30d"].value == 2.0
    assert metrics["over_budget_count"].value == 1.0  # b spent 3.0 of 2.5
    assert metrics["repair_rounds_mean"].value == 0.5  # 2 repairs over 4 tasks with a build
    assert metrics["escalation_rate"].value == 20.0  # d of 5
    review = metrics["review_minutes_median"]
    assert review.value is not None and review.value < 1.0 and review.denominator == 2
    assert metrics["eval_pass_rate"].value == 75.0
    assert metrics["eval_pass_rate_bug"].value == 50.0
    assert metrics["eval_gap_points"].value == -5.0
    assert metrics["protected_path_attempts"].value == 1.0
    assert metrics["stuck_runs_7d"].value == 1.0
    assert metrics["verdict_override_rate"].value is None  # no review phase in the fixture

    day = local_day(NOW, "America/Sao_Paulo")
    stored = w.kpis_for_day(day)
    assert stored["cost_per_merged_pr"]["numerator"] == 4.0
    assert stored["merge_rate"]["value"] == 66.67 and len(stored) == len(metrics)
    again = {m.name: m for m in kpis.materialise(now=NOW)}  # an upsert: same keys, same values
    assert again["merge_rate"].value == 66.67 and len(w.kpis_for_day(day)) == len(metrics)

    assert w.audit_queue() == []  # both drawn tasks are decided
    rows = w.db.fetchall(
        "SELECT task_id, stratum, sampled, decision FROM audit_decisions ORDER BY task_id"
    )
    assert [(r["task_id"], r["stratum"], r["sampled"], r["decision"]) for r in rows] == [
        (a, "risk:low", 1, "disagree"),
        (b, "risk:high", 1, "agree"),
    ]
    assert kpis.sample_month("2026-10") == []  # already drawn: idempotent
    audits = w.db.fetchall("SELECT actor FROM events WHERE kind = 'audit'")
    assert [r["actor"] for r in audits] == ["mlima", "mlima"]
    with pytest.raises(TraceError, match="decided"):
        w.decide_audit(task_id=a, month="2026-10", decision="agree", reason="again", by="mlima")


async def test_nightly_loop_materialises_on_the_day_change(trace_writer: TraceWriter) -> None:
    moment = [NOW]
    kpis = KpiMaterialiser(trace_writer, clock=lambda: moment[0])
    stop = asyncio.Event()
    loop = asyncio.create_task(kpis.run(stop, interval_s=0.05))
    await asyncio.sleep(0.2)
    assert kpis.last_day == "2026-10-24" and trace_writer.kpis_for_day("2026-10-24")
    moment[0] = NOW + timedelta(days=1)
    await asyncio.sleep(0.3)
    assert kpis.last_day == "2026-10-25" and trace_writer.kpis_for_day("2026-10-25")
    stop.set()
    await loop
    logs = trace_writer.db.fetchall(
        "SELECT seq FROM events WHERE kind = 'log' AND payload_json LIKE ?",
        ('%"kpi":"materialised"%',),
    )
    assert len(logs) == 2
