"""Unit 53: the read API serves trace rows with paging, limits, errors and the live tail."""

import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi.testclient import TestClient

from orchestrator.api.app import ApiDeps, create_app
from orchestrator.api.limits import RULES, RateLimiter, identify
from orchestrator.api.read import decode_cursor, encode_cursor, parse_questions
from orchestrator.api.streams import changes_stream, sse_frame, task_events_stream
from orchestrator.intake.webhook import GitHubIpRanges
from orchestrator.models.enums import (
    AgentRole,
    ApprovalGate,
    BlockedReason,
    EventKind,
    GateName,
    OutputType,
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
from orchestrator.trace.retention import purge_login

LOCAL = {"X-Forwarded-For": "127.0.0.1", "X-Factory-Actor": "mlima"}
CONSOLE = {
    "X-Forwarded-For": "10.0.0.5",
    "X-Factory-Actor": "rsouza",
    "X-Factory-Service-Token": "svc",
}
REMOTE = {"X-Forwarded-For": "10.0.0.9", "X-Factory-Actor": "rsouza"}
NOW = datetime(2026, 10, 24, 15, 0, tzinfo=UTC)


def admit(writer: TraceWriter, issue: int, **fields: object) -> str:
    task_id = f"acme/atlas-api#{issue}"
    writer.insert_task(
        task_id=task_id,
        issue_number=issue,
        title=f"task {issue}",
        branch=f"agent/{issue}",
        workspace_kind=WorkspaceKind.WORKTREE,
        budget_usd=6.0,
        admitted_by="rsouza",
        config_sha="cfg1",
    )
    status = fields.get("status")
    if isinstance(status, TaskStatus):
        reason = fields.get("blocked_reason")
        writer.set_task_status(
            task_id, status, blocked_reason=reason if isinstance(reason, BlockedReason) else None
        )
    return task_id


def run_with_phase(
    writer: TraceWriter, task_id: str, name: PhaseName, kind: PhaseKind, status: PhaseStatus
) -> tuple[str, str]:
    run_id = writer.start_run(
        task_id=task_id, attempt=1, factory_version="0.1.0", config_sha="cfg1"
    )
    phase_id = writer.start_phase(
        run_id=run_id, seq=1, name=name, kind=kind, owner="x", timeout_s=60
    )
    if status is not PhaseStatus.RUNNING:
        writer.finish_phase(phase_id, status, cost_usd=0.5)
        writer.finish_run(run_id, RunStatus.SUCCESS)
    return run_id, phase_id


def client_for(
    writer: TraceWriter, *, repo_clone: Path | None = None, limiter: RateLimiter | None = None
) -> TestClient:
    settings = Settings(trusted_proxy_header="X-Forwarded-For", service_token="svc")
    deps = ApiDeps(
        writer=writer,
        settings=settings,
        ip_ranges=GitHubIpRanges((), datetime.now(UTC)),
        repo_clone=repo_clone,
        limiter=limiter or RateLimiter(),
    )
    return TestClient(create_app(deps))


def path(task_id: str, suffix: str = "") -> str:
    return f"/api/v1/tasks/{quote(task_id, safe='')}{suffix}"


def test_callers_cursors_and_helpers() -> None:
    cursor = encode_cursor("2026-10-24T00:00:00.000+00:00", "acme/atlas-api#1")
    assert decode_cursor(cursor) == ["2026-10-24T00:00:00.000+00:00", "acme/atlas-api#1"]
    assert decode_cursor("not-a-cursor") is None
    assert parse_questions("open questions:\n- Which timezone?\n- Calendar days?") == [
        "Which timezone?",
        "Calendar days?",
    ]
    frame = sse_frame("row", {"seq": 3}, id=3)
    assert frame == 'event: row\nid: 3\ndata: {"seq":3}\n\n'
    limiter = RateLimiter(clock=lambda: 0.0)
    assert all(limiter.allow("k", "kill_switch") for _ in range(1))
    assert not limiter.allow("k", "kill_switch")
    assert RULES["read"] == (300, 60.0) and RULES["export"] == (10, 3600.0)


def test_installation_tasks_paging_and_errors(trace_writer: TraceWriter) -> None:
    w = trace_writer
    for issue in range(1, 6):
        admit(w, issue)
    admit(w, 6, status=TaskStatus.BLOCKED, blocked_reason=BlockedReason.OPEN_QUESTIONS)
    client = client_for(w)
    assert client.get("/api/v1/installation").status_code == 403  # nobody
    assert client.get("/api/v1/installation", headers=REMOTE).status_code == 403
    installation = client.get("/api/v1/installation", headers=LOCAL)
    assert installation.status_code == 200
    body = installation.json()
    assert body["org"] == "acme" and body["budget_mode"] == "install"
    assert body["factory_enabled"] is True and "install_id" not in body
    assert body["spent_today_usd"] == 0.0 and body["daily_cap_usd"] == 54.0
    assert body["open_incident_seq"] is None and body["last_kill_switch"] is None
    assert body["last_dry_run"] is None
    trace_writer.write_event(
        EventKind.LOG,
        "orchestrator",
        payload={
            "dry_run": "derived",
            "issues": [{"issue": 1, "completed": True, "cost_usd": 0.4}],
            "completed": 1,
            "required": 2,
            "highest_cost_usd": 0.4,
            "factor": 2.0,
            "per_task_budget_usd": 5.0,
            "floor_usd": 5.0,
            "reason": "the industry per-task cap 5.00",
        },
    )
    again = client.get("/api/v1/installation", headers=LOCAL).json()
    assert again["last_dry_run"]["per_task_budget_usd"] == 5.0
    assert again["last_dry_run"]["issues"][0]["issue"] == 1
    assert client.get("/api/v1/installation", headers=CONSOLE).status_code == 200  # service token

    first = client.get("/api/v1/tasks?limit=4", headers=LOCAL).json()
    assert len(first["items"]) == 4 and first["next"] is not None
    second = client.get(f"/api/v1/tasks?limit=4&cursor={first['next']}", headers=LOCAL).json()
    assert len(second["items"]) == 2 and second["next"] is None
    seen = [t["task_id"] for t in first["items"] + second["items"]]
    assert len(set(seen)) == 6
    blocked = client.get("/api/v1/tasks?status=blocked", headers=LOCAL).json()
    assert [t["issue_number"] for t in blocked["items"]] == [6]
    assert blocked["items"][0]["blocked_reason"] == "open_questions"
    for bad in ("?status=nope", "?limit=0", "?limit=201", "?cursor=zzz"):
        response = client.get(f"/api/v1/tasks{bad}", headers=LOCAL)
        assert response.status_code == 400
        error = response.json()
        assert set(error) >= {"code", "message", "traceId"} and error["code"].startswith("tasks_")


def test_task_detail_events_envelopes_gates_and_review(
    trace_writer: TraceWriter, product: Path
) -> None:
    w = trace_writer
    task = admit(w, 418)
    run_id, phase_id = run_with_phase(w, task, PhaseName.BUILD, PhaseKind.AGENT, PhaseStatus.PASS)
    w.set_task_pr(task, pr_number=431, head_sha="a" * 40)
    w.set_task_spec(task, spec_path="specs/418-invoice-due-date.md", spec_sha="b" * 40)
    seqs = [
        w.write_event(
            EventKind.TOOL_CALL,
            "builder",
            task_id=task,
            run_id=run_id,
            phase_id=phase_id,
            payload={"tool": "Bash", "ok": True, "n": n},
        )
        for n in range(3)
    ]
    w.write_envelope(
        run_id=run_id,
        phase_id=phase_id,
        agent=AgentRole.REVIEWER,
        output_type=OutputType.REVIEW,
        payload_json='{"verdict":"accepted","findings":[]}',
        valid=True,
        attempt=1,
    )
    w.write_gate_result(
        run_id=run_id,
        phase_id=phase_id,
        attempt=1,
        gate=GateName("lint"),
        passed=False,
        checks=[{"item": "ruff", "ok": False, "note": "E501"}],
        command="ruff check .",
        exit_code=1,
        output_tail="E501 line too long",
    )
    w.record_approval(
        task_id=task,
        gate=ApprovalGate.PLAN,
        actor_login="rsouza",
        bound_sha="b" * 40,
        github_event_id="d1",
        phase_id=phase_id,
    )
    client = client_for(w, repo_clone=product)

    assert client.get(path("acme/atlas-api#999"), headers=LOCAL).status_code == 404
    detail = client.get(path(task), headers=LOCAL).json()
    assert detail["task"]["pr_number"] == 431 and len(detail["runs"]) == 1
    assert [p["name"] for p in detail["phases"]] == ["build"]
    assert (
        detail["approvals"][0]["actor_login"] == "rsouza"
        and detail["approvals"][0]["codeowner"] is None
    )
    assert detail["budget"] == {
        "budget_usd": 6.0,
        "spent_usd": 0.5,
        "remaining_usd": 5.5,
        "raised": False,
        "last_stop": None,
    }
    assert detail["links"]["pr"] == "https://github.com/acme/atlas-api/pull/431"
    assert detail["links"]["spec"].endswith("/blob/agent/418/specs/418-invoice-due-date.md")
    assert detail["chain"]["verified"] is True and detail["chain"]["records"] >= 3

    page = client.get(path(task, "/events?limit=2"), headers=LOCAL).json()
    assert [e["seq"] for e in page["items"]] == seqs[:2] and page["next"] == str(seqs[1])
    rest = client.get(path(task, f"/events?limit=2&after={page['next']}"), headers=LOCAL).json()
    assert [e["seq"] for e in rest["items"]] == seqs[2:] and rest["next"] is None
    assert rest["items"][0]["payload"] == {"tool": "Bash", "ok": True, "n": 2}
    only = client.get(path(task, "/events?kind=tool_call"), headers=LOCAL).json()
    assert len(only["items"]) == 3
    assert client.get(path(task, "/events?after=x"), headers=LOCAL).status_code == 400
    newest = client.get(path(task, f"/events?before={seqs[2] + 1}&limit=2"), headers=LOCAL).json()
    assert [e["seq"] for e in newest["items"]] == [seqs[2], seqs[1]]
    assert newest["next"] == str(seqs[1])
    older = client.get(path(task, f"/events?before={newest['next']}&limit=2"), headers=LOCAL).json()
    assert [e["seq"] for e in older["items"]] == [seqs[0]] and older["next"] is None
    assert client.get(path(task, "/events?before=x"), headers=LOCAL).status_code == 400
    assert detail["chain"]["head_seq"] >= seqs[2]

    envelopes = client.get(path(task, "/envelopes"), headers=LOCAL).json()["items"]
    assert envelopes[0]["payload"] == {"verdict": "accepted", "findings": []}
    assert envelopes[0]["valid"] is True and envelopes[0]["output_type"] == "ReviewOutput"
    gates = client.get(path(task, "/gates"), headers=LOCAL).json()["items"]
    assert gates[0]["gate"] == "lint" and gates[0]["passed"] is False
    assert gates[0]["checks"] == [{"item": "ruff", "ok": False, "note": "E501"}]
    assert gates[0]["output_tail"] == "E501 line too long"

    pending = client.get(path(task, "/review"), headers=LOCAL)
    assert pending.status_code == 409 and pending.json()["code"] == "review_pending"
    for round_number, verdict in ((1, "revision_needed"), (2, "accepted")):
        w.write_event(
            EventKind.LOG,
            "orchestrator",
            task_id=task,
            run_id=run_id,
            phase_id=phase_id,
            payload={
                "verdict_policy": {
                    "verdict": verdict,
                    "reviewer_verdict": verdict,
                    "conformance": 70.0 + round_number,
                    "blocking_score": 30.0 - round_number,
                    "strongest": None,
                    "findings": [{"requirement_id": "AC1", "score": 12.0}],
                    "dismissed": [],
                    "human_reasons": [],
                    "missing_ac": [],
                    "policy_version": 1,
                    "accept_conformance": 70.0,
                    "revisions_so_far": round_number - 1,
                    "overridden_by": None,
                    "override_reason": None,
                }
            },
        )
    again = client.get(path(task), headers=LOCAL).json()  # the chain grew: verified from the cache
    assert again["chain"]["verified"] is True
    assert again["chain"]["records"] > detail["chain"]["records"]
    assert again["chain"]["head_seq"] > detail["chain"]["head_seq"]
    latest = client.get(path(task, "/review"), headers=LOCAL).json()
    assert (latest["round"], latest["rounds"], latest["verdict"]) == (2, 2, "accepted")
    assert latest["accept_conformance"] == 70.0
    assert latest["conformance"] == 72.0 and latest["envelope"]["verdict"] == "accepted"
    first = client.get(path(task, "/review?round=1"), headers=LOCAL).json()
    assert first["verdict"] == "revision_needed" and first["findings"][0]["requirement_id"] == "AC1"
    assert client.get(path(task, "/review?round=3"), headers=LOCAL).status_code == 400


def test_approvals_audit_kpis_counts_eval_and_config(
    trace_writer: TraceWriter, product: Path, tmp_path: Path
) -> None:
    w = trace_writer
    waiting = admit(w, 1)
    run_id, phase_id = run_with_phase(
        w, waiting, PhaseName.PLAN_APPROVAL, PhaseKind.ENGINEER, PhaseStatus.RUNNING
    )
    w.set_task_spec(waiting, spec_path="README.md", spec_sha="c" * 40)
    w.set_task_risk(waiting, risk="risk:high", score=109.2, governed_path=None)
    w.write_event(
        EventKind.LOG,
        "planner",
        task_id=waiting,
        run_id=run_id,
        phase_id=phase_id,
        payload={"risk": {"score": 109.2, "equation": "70 x 1.3 x 1.2 x 1 = 109.2"}},
    )
    w.write_gate_result(
        run_id=run_id,
        phase_id=phase_id,
        attempt=1,
        gate=GateName("spec_ac_holdout_map"),
        passed=True,
        checks=[{"item": "AC1", "ok": True, "note": "tests/acceptance/test_x.py::test_a"}],
    )
    asked = admit(w, 2, status=TaskStatus.BLOCKED, blocked_reason=BlockedReason.OPEN_QUESTIONS)
    w.write_event(
        EventKind.ERROR,
        "orchestrator",
        task_id=asked,
        payload={
            "blocked_reason": "open_questions",
            "evidence": (
                "open questions:\n- Qual é o fuso horário que devemos usar para o vencimento?"
                "\n- Os dias são corridos ou úteis, e não contam os feriados?"
            ),
        },
    )
    admit(w, 3, status=TaskStatus.BLOCKED, blocked_reason=BlockedReason.BUDGET)
    admit(w, 4, status=TaskStatus.BLOCKED, blocked_reason=BlockedReason.HUMAN_DECISION_NEEDED)
    merged = admit(w, 5)
    w.set_task_pr(merged, pr_number=432, head_sha="d" * 40)
    w.set_task_status(merged, TaskStatus.MERGED)
    w.write_event(  # what the merge observer logs when GitHub reports the merge
        EventKind.LOG,
        "orchestrator",
        task_id=merged,
        payload={"merged": 432, "head_sha": "d" * 40, "by": "rsouza", "github_event_id": "m"},
    )
    w.insert_audit_decision(
        task_id=merged, pr_number=432, month="2026-10", stratum="risk:low", sampled=True
    )
    set_id = w.create_eval_set(name="pilot", repo="atlas-api", reference_pass_rate=80.0)
    w.add_eval_item(set_id, issue_number=101, eval_class="bug")
    eval_run = w.start_eval_run(
        set_id=set_id,
        factory_version="0.1.0",
        config_sha="cfg1",
        trigger="manual",
        trigger_ref=None,
        baseline_run_id=None,
    )
    w.record_eval_result(
        eval_run,
        item_id=w.eval_items(set_id)[0]["id"],
        passed=True,
        cost_usd=0.2,
        duration_s=3,
        failure_reason=None,
    )
    w.finish_eval_run(eval_run, status="complete", pass_rate=100.0, per_class_json='{"bug": 100.0}')
    KpiMaterialiser(w, clock=lambda: NOW).materialise(now=NOW)
    client = client_for(w, repo_clone=product)

    plans = client.get("/api/v1/approvals/plans", headers=LOCAL).json()["items"]
    assert len(plans) == 1 and plans[0]["task_id"] == waiting and plans[0]["risk"] == "risk:high"
    assert (
        plans[0]["risk_breakdown"]["equation"].startswith("70 x")
        and plans[0]["phase_id"] == phase_id
    )
    assert plans[0]["ac_holdout"][0]["item"] == "AC1"
    assert plans[0]["spec_text"] is None  # agent/1 has no README on the branch yet
    questions = client.get("/api/v1/approvals/questions", headers=LOCAL).json()["items"]
    assert questions == [
        {
            "task_id": asked,
            "issue_number": 2,
            "title": "task 2",
            "questions": [
                "Qual é o fuso horário que devemos usar para o vencimento?",
                "Os dias são corridos ou úteis, e não contam os feriados?",
            ],
            "language": "pt-BR",
            "blocked_at": questions[0]["blocked_at"],
        }
    ]
    queue = client.get("/api/v1/audit/queue?month=2026-10", headers=LOCAL).json()["items"]
    assert queue[0]["task_id"] == merged and queue[0]["pr"] == 432 and queue[0]["sampled"] is True
    assert client.get("/api/v1/audit/queue?month=2026-13-01", headers=LOCAL).status_code == 400

    kpi = client.get("/api/v1/kpis", headers=LOCAL).json()
    names = {m["metric"]: m for m in kpi["items"]}
    assert kpi["window"] == "30d" and kpi["day"] == "2026-10-24"
    assert names["merge_rate"]["target"] == ">= 70%" and "unattended_rate" in names
    assert names["cost_per_merged_pr"]["denominator"] == 1.0
    assert "repair_rounds_mean" not in names
    assert client.get("/api/v1/kpis?window=7d", headers=LOCAL).status_code == 400
    indicators = {
        m["metric"]: m for m in client.get("/api/v1/indicators", headers=LOCAL).json()["items"]
    }
    assert indicators["repair_rounds_mean"]["thresholds"] == {"target": 1.5, "review": 2.5}
    assert indicators["eval_gap_points"]["value"] == 20.0 and "merge_rate" not in indicators
    counts = client.get("/api/v1/needs-human/count", headers=LOCAL).json()
    assert counts == {"plans": 1, "questions": 1, "decisions": 1, "budget": 1}

    sets = client.get("/api/v1/eval/sets", headers=LOCAL).json()["items"]
    assert (
        sets[0]["name"] == "pilot"
        and sets[0]["items"] == 1
        and sets[0]["reference_pass_rate"] == 80.0
    )
    runs = client.get(f"/api/v1/eval/runs?set_id={set_id}", headers=LOCAL).json()["items"]
    assert runs[0]["pass_rate"] == 100.0 and runs[0]["per_class"] == {"bug": 100.0}
    one = client.get(f"/api/v1/eval/runs/{eval_run}", headers=LOCAL).json()
    assert one["results"][0]["issue_number"] == 101 and one["results"][0]["passed"] is True
    assert client.get("/api/v1/eval/runs/nope", headers=LOCAL).status_code == 404

    config = client.get("/api/v1/config", headers=LOCAL).json()
    assert config["config_sha"] == "cfg1" and config["vendors"]["builder"] == "anthropic"
    history = client.get("/api/v1/config/history", headers=LOCAL).json()["items"]
    assert history[0]["config_sha"] == "cfg1" and history[0]["current"] is True
    assert history[0]["eval"]["id"] == eval_run


def test_rate_limits_and_purged_logins(trace_writer: TraceWriter) -> None:
    w = trace_writer
    task = admit(w, 7)
    w.write_event(EventKind.APPROVAL, "rsouza", task_id=task, payload={"actor_login": "rsouza"})
    purge_login(w, "rsouza", actor="mlima")
    ticks = [0.0]
    client = client_for(w, limiter=RateLimiter(clock=lambda: ticks[0]))
    for _ in range(RULES["count"][0]):
        assert client.get("/api/v1/needs-human/count", headers=LOCAL).status_code == 200
    limited = client.get("/api/v1/needs-human/count", headers=LOCAL)
    assert limited.status_code == 429 and limited.json()["detail"]["rule"] == "count"
    assert "600 per 60 s per login" in limited.json()["message"]
    assert (
        client.get("/api/v1/needs-human/count", headers=CONSOLE).status_code == 200
    )  # another login
    ticks[0] = 61.0
    assert client.get("/api/v1/needs-human/count", headers=LOCAL).status_code == 200
    events = client.get(path(task, "/events"), headers=LOCAL).json()["items"]
    assert events[-1]["actor"].startswith("user:") and "rsouza" not in json.dumps(events)


async def test_tail_delivers_a_row_within_two_seconds_and_resumes(
    trace_writer: TraceWriter,
) -> None:
    w = trace_writer
    task = admit(w, 8)
    first = w.write_event(EventKind.LOG, "orchestrator", task_id=task, payload={"n": 1})
    closed: list[bool] = []
    frames: list[str] = []
    second_seen = asyncio.Event()

    async def consume() -> None:
        stream = task_events_stream(
            w,
            task,
            after=0,
            purged=frozenset(),
            on_close=lambda: closed.append(True),
            poll_s=0.05,
            ping_s=0.3,
        )
        try:
            async for frame in stream:
                frames.append(frame)
                if frame.startswith("event: row") and f"id: {first}" not in frame:
                    second_seen.set()
                    break
        finally:
            await stream.aclose()  # what a dropped connection does through the response

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0.5)  # longer than ping_s: a ping goes out through the silence
    started = time.monotonic()
    second = w.write_event(EventKind.LOG, "orchestrator", task_id=task, payload={"n": 2})
    await asyncio.wait_for(second_seen.wait(), 5)
    elapsed = time.monotonic() - started
    await asyncio.wait_for(consumer, 5)
    kinds = [f.split("\n", 1)[0] for f in frames]
    assert kinds[0] == "event: row" and f"id: {first}" in frames[0]
    assert "event: ping" in kinds and frames[-1].startswith("event: row")
    assert f"id: {second}" in frames[-1] and elapsed < 2.0
    assert closed == [True]

    resumed: list[str] = []
    stream = task_events_stream(
        w, task, after=first, purged=frozenset(), on_close=lambda: None, poll_s=0.05
    )
    async for frame in stream:
        resumed.append(frame)
        break
    await stream.aclose()
    assert f"id: {second}" in resumed[0]


async def test_changes_stream_reports_tasks_evals_and_configs(trace_writer: TraceWriter) -> None:
    w = trace_writer
    got: list[str] = []

    async def consume() -> None:
        async for frame in changes_stream(w, on_close=lambda: None, poll_s=0.05, ping_s=0.2):
            got.append(frame)
            if sum(1 for f in got if not f.startswith("event: ping")) >= 3:
                break

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0.1)
    task = admit(w, 9)
    w.write_event(EventKind.LOG, "orchestrator", task_id=task, payload={"x": 1})
    set_id = w.create_eval_set(name="s", repo="r")
    run_id = w.start_eval_run(
        set_id=set_id,
        factory_version="0.1.0",
        config_sha="cfg1",
        trigger="manual",
        trigger_ref=None,
        baseline_run_id=None,
    )
    w.finish_eval_run(run_id, status="complete", pass_rate=50.0, per_class_json="{}")
    w.load_config_version(
        config_sha="cfg2",
        main_commit="def",
        roster_json="{}",
        risk_yaml="",
        review_policy_yaml="",
        protected_paths_json="[]",
        quality_commands_json="{}",
        vendors_json='{"planner":"anthropic","builder":"anthropic","reviewer":"openai"}',
    )
    await asyncio.wait_for(consumer, 5)
    notices = [f for f in got if not f.startswith("event: ping")]
    assert any(f.startswith("event: task_changed") and task in f for f in notices)
    assert any(f.startswith("event: eval_complete") and run_id in f for f in notices)
    assert any(f.startswith("event: config_loaded") and "cfg2" in f for f in notices)


def test_stream_routes_guard_callers_and_count_streams(trace_writer: TraceWriter) -> None:
    w = trace_writer
    task = admit(w, 10)
    limiter = RateLimiter()
    client = client_for(w, limiter=limiter)
    assert client.get(path(task, "/events/stream")).status_code == 403
    assert (
        client.get(path("acme/atlas-api#999", "/events/stream"), headers=LOCAL).status_code == 404
    )
    assert client.get(path(task, "/events/stream?after=x"), headers=LOCAL).status_code == 400
    assert client.get("/api/v1/changes").status_code == 403
    with client.stream("GET", path(task, "/events/stream"), headers=LOCAL) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert limiter.streams("mlima") == 1
        w.write_event(EventKind.LOG, "orchestrator", task_id=task, payload={"live": True})
        for line in response.iter_lines():
            if line.startswith("data:"):
                assert json.loads(line[5:])["payload"] == {"live": True}
                break
    for _ in range(20):
        if limiter.streams("mlima") == 0:
            break
        time.sleep(0.05)
    assert limiter.streams("mlima") == 0
    settings = Settings(trusted_proxy_header="X-Forwarded-For", service_token="svc")
    scope = {
        "type": "http",
        "headers": [
            (b"x-forwarded-for", b"10.0.0.5"),
            (b"x-factory-service-token", b"svc"),
            (b"x-factory-actor", b"rsouza"),
        ],
        "client": ("testclient", 1),
        "method": "GET",
        "path": "/",
        "query_string": b"",
    }
    from fastapi import Request

    caller = identify(Request(scope), settings)
    assert (caller.actor, caller.via, caller.key) == ("rsouza", "service_token", "rsouza")
