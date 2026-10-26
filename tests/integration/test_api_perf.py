"""Unit 53: the Overview reads answer under 2 s with 1,000 seeded tasks (TRD §3.4)."""

import json
import time
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from orchestrator.api.app import ApiDeps, create_app
from orchestrator.intake.webhook import GitHubIpRanges
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
from orchestrator.models.settings import Settings
from orchestrator.trace import TraceWriter
from orchestrator.trace.kpi import KpiMaterialiser

LOCAL = {"X-Forwarded-For": "127.0.0.1", "X-Factory-Actor": "mlima"}
OVERVIEW = (
    "/api/v1/tasks?limit=200",
    "/api/v1/tasks?status=running",
    "/api/v1/kpis",
    "/api/v1/indicators",
    "/api/v1/needs-human/count",
    "/api/v1/approvals/plans",
    "/api/v1/approvals/questions",
    "/api/v1/audit/queue?month=2026-10",
    "/api/v1/installation",
)


def seed(writer: TraceWriter, count: int = 1000) -> None:
    reasons = list(BlockedReason)
    for n in range(1, count + 1):
        task_id = f"acme/atlas-api#{n}"
        writer.insert_task(
            task_id=task_id,
            issue_number=n,
            title=f"task {n}",
            branch=f"agent/{n}",
            workspace_kind=WorkspaceKind.WORKTREE,
            budget_usd=6.0,
            admitted_by="rsouza",
            config_sha="cfg1",
        )
        bucket = n % 10
        if bucket < 5:
            writer.set_task_pr(task_id, pr_number=1000 + n, head_sha="a" * 40)
            writer.set_task_status(task_id, TaskStatus.MERGED if bucket < 3 else TaskStatus.PR_OPEN)
        elif bucket < 7:
            writer.set_task_status(
                task_id, TaskStatus.BLOCKED, blocked_reason=reasons[n % len(reasons)]
            )
        elif bucket == 7:
            writer.set_task_status(task_id, TaskStatus.RUNNING)
        if n % 5 == 0:
            run_id = writer.start_run(
                task_id=task_id, attempt=1, factory_version="0.1.0", config_sha="cfg1"
            )
            phase_id = writer.start_phase(
                run_id=run_id,
                seq=1,
                name=PhaseName.BUILD,
                kind=PhaseKind.AGENT,
                owner="builder",
                timeout_s=60,
            )
            writer.finish_phase(phase_id, PhaseStatus.PASS, cost_usd=0.4)
            writer.finish_run(run_id, RunStatus.SUCCESS)
            writer.write_event(
                EventKind.LOG, "orchestrator", task_id=task_id, payload={"merged": 1000 + n}
            )
            if n % 50 == 0:
                writer.insert_audit_decision(
                    task_id=task_id,
                    pr_number=1000 + n,
                    month="2026-10",
                    stratum="risk:low",
                    sampled=True,
                )


def test_overview_reads_under_two_seconds_with_a_thousand_tasks(trace_writer: TraceWriter) -> None:
    started = time.monotonic()
    seed(trace_writer)
    KpiMaterialiser(
        trace_writer, clock=lambda: datetime(2026, 10, 24, 15, 0, tzinfo=UTC)
    ).materialise()
    seeded_in = time.monotonic() - started
    settings = Settings(trusted_proxy_header="X-Forwarded-For")
    client = TestClient(
        create_app(
            ApiDeps(
                writer=trace_writer,
                settings=settings,
                ip_ranges=GitHubIpRanges((), datetime.now(UTC)),
            )
        )
    )
    timings: dict[str, float] = {}
    for url in OVERVIEW:
        began = time.monotonic()
        response = client.get(url, headers=LOCAL)
        timings[url] = round(time.monotonic() - began, 3)
        assert response.status_code == 200, (url, response.text)
    page = client.get("/api/v1/tasks?limit=200", headers=LOCAL).json()
    assert len(page["items"]) == 200 and page["next"] is not None
    print(f"GATE-EVIDENCE api.overview: {json.dumps({'seed_s': round(seeded_in, 1), **timings})}")
    assert all(t < 2.0 for t in timings.values()), timings
