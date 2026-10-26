"""Unit 55: every write passes caller, permission, authz, the key, the bounds and one control."""

import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi.testclient import TestClient

from orchestrator.api.app import ApiDeps, create_app
from orchestrator.api.controls import ControlDeps
from orchestrator.api.limits import RateLimiter
from orchestrator.intake.webhook import GitHubIpRanges
from orchestrator.models.enums import BlockedReason, EventKind, TaskStatus, WorkspaceKind
from orchestrator.models.settings import Settings
from orchestrator.trace import TraceWriter
from orchestrator.trace.export import public_key_b64, verify_export
from orchestrator.watchdogs import KillSwitch

PERMISSIONS = {"rsouza": "write", "reader": "read", "mlima": "admin", "ghost": None}
TASK = "acme/atlas-api#418"


def headers(actor: str, key: str = "k1") -> dict[str, str]:
    return {
        "X-Forwarded-For": "10.0.0.5",
        "X-Factory-Service-Token": "svc",
        "X-Factory-Actor": actor,
        "Idempotency-Key": key,
    }


def admit(writer: TraceWriter, issue: int = 418, **status: object) -> str:
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
    blocked = status.get("blocked")
    if isinstance(blocked, BlockedReason):
        writer.set_task_status(task_id, TaskStatus.BLOCKED, blocked_reason=blocked)
    return task_id


async def permission_for(login: str) -> str | None:
    return PERMISSIONS.get(login, "none")


async def codeowner_of(login: str, path: str) -> bool:
    return login == "mlima" and path.startswith("src/billing/")


def client_for(
    writer: TraceWriter,
    *,
    kill_switch: KillSwitch | None = None,
    limiter: RateLimiter | None = None,
) -> tuple[TestClient, ControlDeps]:
    settings = Settings(trusted_proxy_header="X-Forwarded-For", service_token="svc")
    shared = limiter or RateLimiter()
    controls = ControlDeps(
        writer=writer,
        settings=settings,
        limiter=shared,
        permission_for=permission_for,
        codeowner_of=codeowner_of,
        kill_switch=kill_switch,
    )
    deps = ApiDeps(
        writer=writer,
        settings=settings,
        ip_ranges=GitHubIpRanges((), datetime.now(UTC)),
        limiter=shared,
        kill_switch=kill_switch,
        controls=controls,
    )
    return TestClient(create_app(deps)), controls


def controls_written(writer: TraceWriter) -> list[dict[str, object]]:
    rows = writer.db.fetchall(
        "SELECT actor, payload_json FROM events WHERE kind = 'control' ORDER BY seq"
    )
    return [{"actor": r["actor"], **json.loads(r["payload_json"])} for r in rows]


def test_budget_raise_gate_order_and_idempotency(trace_writer: TraceWriter) -> None:
    w = trace_writer
    admit(w)
    client, _ = client_for(w)
    path = f"/api/v1/tasks/{quote(TASK, safe='')}/budget"
    body = {"budget_usd": 9.0, "reason": "long spec"}
    assert client.post(path, json=body).status_code == 403  # nobody
    no_key = {k: v for k, v in headers("rsouza").items() if k != "Idempotency-Key"}
    assert client.post(path, json=body, headers=no_key).status_code == 400
    denied = client.post(path, json=body, headers=headers("reader"))
    assert denied.status_code == 403 and denied.json()["code"] == "budget_raise_denied"
    unknown = client.post(path, json=body, headers=headers("ghost"))
    assert unknown.status_code == 403 and "unreachable" in unknown.json()["message"]
    bad = client.post(path, json={"budget_usd": "nine"}, headers=headers("rsouza"))
    assert bad.status_code == 400 and bad.json()["code"] == "body"
    first = client.post(path, json=body, headers=headers("rsouza"))
    assert first.status_code == 200 and first.json()["new"] == 9.0
    replay = client.post(path, json=body, headers=headers("rsouza"))
    assert replay.status_code == 200 and replay.json() == first.json()
    other_body = client.post(
        path, json={"budget_usd": 9.5, "reason": "x"}, headers=headers("rsouza")
    )
    assert other_body.status_code == 422 and other_body.json()["code"] == "idempotency_body"
    again = client.post(
        path, json={"budget_usd": 10.0, "reason": "x"}, headers=headers("rsouza", "k2")
    )
    assert again.status_code == 409  # raised once already
    too_much = client.post(
        f"/api/v1/tasks/{quote(admit(w, 419), safe='')}/budget",
        json={"budget_usd": 100.0, "reason": "x"},
        headers=headers("rsouza", "k3"),
    )
    assert too_much.status_code == 422
    written = controls_written(w)
    assert [
        (c["actor"], c.get("attempted", c["action"]), c["action"] == "denied") for c in written
    ] == [
        ("reader", "budget_raise", True),
        ("ghost", "budget_raise", True),
        ("rsouza", "budget_raise", False),
    ]
    task = w.get_task(TASK)
    assert task is not None and task["budget_usd"] == 9.0


def test_concurrency_admissions_and_kill_switch_bounds(trace_writer: TraceWriter) -> None:
    w = trace_writer
    ticks = [0.0]
    client, _ = client_for(
        w, kill_switch=KillSwitch(w), limiter=RateLimiter(clock=lambda: ticks[0])
    )
    out = client.post(
        "/api/v1/controls/concurrency", json={"n": 11, "reason": "x"}, headers=headers("mlima")
    )
    assert out.status_code == 422
    ok = client.post(
        "/api/v1/controls/concurrency",
        json={"n": 5, "reason": "more"},
        headers=headers("mlima", "c2"),
    )
    assert ok.status_code == 200 and ok.json()["old"] == 3 and ok.json()["new"] == 5
    installation = w.get_installation()
    assert installation is not None and installation["concurrency_n"] == 5

    paused = client.post(
        "/api/v1/controls/admissions",
        json={"paused": 1, "reason": "upgrade"},
        headers=headers("mlima", "a1"),
    )
    assert paused.status_code == 200 and w.admissions_paused() is True
    again = client.post(
        "/api/v1/controls/admissions",
        json={"paused": 1, "reason": "upgrade"},
        headers=headers("mlima", "a2"),
    )
    assert again.status_code == 409
    resumed = client.post(
        "/api/v1/controls/admissions",
        json={"paused": 0, "reason": "done", "clear_incident": 7},
        headers=headers("mlima", "a3"),
    )
    assert resumed.status_code == 200 and w.admissions_paused() is False

    off = client.post(
        "/api/v1/controls/kill-switch",
        json={"enabled": 0, "reason": "drill"},
        headers=headers("mlima", "s1"),
    )
    assert off.status_code == 200 and off.json()["factory_enabled"] is False
    limited = client.post(
        "/api/v1/controls/kill-switch",
        json={"enabled": 1, "reason": "back"},
        headers=headers("mlima", "s2"),
    )
    assert limited.status_code == 429 and limited.json()["detail"]["rule"] == "kill_switch"
    ticks[0] = 61.0
    on = client.post(
        "/api/v1/controls/kill-switch",
        json={"enabled": 1, "reason": "back"},
        headers=headers("mlima", "s2"),
    )
    assert on.status_code == 200 and w.factory_enabled() is True
    same = client.post(
        "/api/v1/controls/kill-switch",
        json={"enabled": 1, "reason": "back"},
        headers=headers("mlima", "s3"),
    )
    assert same.status_code == 429  # one a minute, before the state check
    actions = [(c["actor"], c["action"]) for c in controls_written(w) if c["action"] != "denied"]
    assert actions.count(("mlima", "concurrency")) == 1
    assert actions.count(("mlima", "admissions")) == 2
    assert (
        sum(1 for a in actions if a[1] in ("kill_switch_off", "kill_switch_on", "kill_switch")) >= 2
    )


def test_risk_override_audit_and_eval_runs(trace_writer: TraceWriter) -> None:
    w = trace_writer
    task = admit(w)
    w.set_task_risk(task, risk="risk:low", score=40.0, governed_path=None)
    client, controls = client_for(w)
    path = f"/api/v1/tasks/{quote(task, safe='')}/risk-override"
    body = {"risk": "risk:high", "reason": "touches billing"}
    overridden = client.post(path, json=body, headers=headers("rsouza"))
    assert overridden.status_code == 200 and overridden.json()["risk"] == "risk:high"
    row = w.get_task(task)
    assert row is not None and row["risk"] == "risk:high" and row["risk_overridden_by"] == "rsouza"
    approvals = w.db.fetchall(
        "SELECT gate, actor_login, reason FROM approvals WHERE task_id = ?", (task,)
    )
    assert [(a["gate"], a["actor_login"]) for a in approvals] == [("risk_override", "rsouza")]
    w.set_task_pr(task, pr_number=431, head_sha="a" * 40)
    w.set_task_status(task, TaskStatus.PR_OPEN)
    late = client.post(
        path, json={"risk": "risk:governed", "reason": "late"}, headers=headers("rsouza", "r2")
    )
    assert late.status_code == 409
    missing = client.post(
        f"/api/v1/tasks/{quote('acme/atlas-api#999', safe='')}/risk-override",
        json=body,
        headers=headers("rsouza", "r3"),
    )
    assert missing.status_code == 404

    w.set_task_status(task, TaskStatus.MERGED)
    w.insert_audit_decision(
        task_id=task, pr_number=431, month="2026-10", stratum="risk:high", sampled=True
    )
    audit = f"/api/v1/audit/{quote(task, safe='')}"
    decided = client.post(
        audit,
        json={"decision": "agree", "reason": "fine", "month": "2026-10"},
        headers=headers("mlima"),
    )
    assert decided.status_code == 200 and decided.json()["decision"] == "agree"
    twice = client.post(
        audit,
        json={"decision": "disagree", "reason": "x", "month": "2026-10"},
        headers=headers("mlima", "d2"),
    )
    assert twice.status_code == 409
    not_drawn = client.post(
        audit,
        json={"decision": "agree", "reason": "x", "month": "2026-09"},
        headers=headers("mlima", "d3"),
    )
    assert not_drawn.status_code == 404
    reader = client.post(
        audit, json={"decision": "agree", "reason": "x"}, headers=headers("reader", "d4")
    )
    assert reader.status_code == 403

    started: list[tuple[str, str]] = []

    async def start_eval(set_id: str, actor: str) -> str:
        started.append((set_id, actor))
        return "run-1"

    controls.start_eval = start_eval
    assert (
        client.post(
            "/api/v1/eval/runs", json={"set_id": "nope"}, headers=headers("mlima", "e1")
        ).status_code
        == 404
    )
    set_id = w.create_eval_set(name="small", repo="atlas-api")
    for issue in range(1, 6):
        w.add_eval_item(set_id, issue_number=issue, eval_class="bug")
    small = client.post(
        "/api/v1/eval/runs", json={"set_id": set_id}, headers=headers("mlima", "e2")
    )
    assert small.status_code == 422
    for issue in range(6, 26):
        w.add_eval_item(set_id, issue_number=issue, eval_class="docs")
    accepted = client.post(
        "/api/v1/eval/runs", json={"set_id": set_id}, headers=headers("rsouza", "e3")
    )
    assert accepted.status_code == 202 and accepted.json()["run_id"] == "run-1"
    assert started == [(set_id, "rsouza")]
    w.start_eval_run(
        set_id=set_id,
        factory_version="0.1.0",
        config_sha="cfg1",
        trigger="manual",
        trigger_ref=None,
        baseline_run_id=None,
    )
    busy = client.post(
        "/api/v1/eval/runs", json={"set_id": set_id}, headers=headers("rsouza", "e4")
    )
    assert busy.status_code == 409
    assert (
        w.db.scalar(
            "SELECT COUNT(*) FROM events WHERE kind = 'control'"
            " AND payload_json LIKE '%\"eval_run\"%'"
        )
        == 1
    )


def test_old_routes_moved_and_export_needs_the_action(trace_writer: TraceWriter) -> None:
    w = trace_writer
    admit(w)
    client, _ = client_for(w)
    assert client.post("/controls/kill-switch", json={}).status_code == 404
    assert client.post(f"/tasks/{quote(TASK, safe='')}/budget", json={}).status_code == 404
    assert client.get(f"/tasks/{quote(TASK, safe='')}/export").status_code == 404
    export = client.get(f"/api/v1/tasks/{quote(TASK, safe='')}/export", headers=headers("reader"))
    assert export.status_code in (
        403,
        503,
    )  # a reader may not export; without a key the route is 503
    events = w.db.fetchall("SELECT kind FROM events WHERE kind = 'export'")
    assert events == []


def test_export_route_needs_the_export_action_and_a_key(
    trace_writer: TraceWriter, tmp_path: Path
) -> None:
    w = trace_writer
    task = admit(w)
    w.write_event(EventKind.LOG, "orchestrator", task_id=task, payload={"x": 1})
    private = ed25519.Ed25519PrivateKey.generate()
    key_file = tmp_path / "export.key"
    key_file.write_bytes(
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    pub = public_key_b64(private)
    w.set_export_pubkey(pub)
    settings = Settings(trusted_proxy_header="X-Forwarded-For", service_token="svc")
    for export_settings in (settings, settings.model_copy(update={"export_key_file": key_file})):
        controls = ControlDeps(
            writer=w, settings=export_settings, limiter=RateLimiter(), permission_for=permission_for
        )
        client = TestClient(
            create_app(
                ApiDeps(
                    writer=w,
                    settings=export_settings,
                    ip_ranges=GitHubIpRanges((), datetime.now(UTC)),
                    controls=controls,
                )
            )
        )
        path = f"/api/v1/tasks/{quote(task, safe='')}/export"
        assert client.get(path).status_code == 403  # nobody
        reader = client.get(path, headers=headers("reader"))
        assert reader.status_code == 403 and reader.json()["code"] == "export_denied"
        missing = client.get(
            f"/api/v1/tasks/{quote('acme/atlas-api#9', safe='')}/export", headers=headers("rsouza")
        )
        assert missing.status_code == 404
        response = client.get(path, headers=headers("rsouza"))
        if export_settings.export_key_file is None:
            assert response.status_code == 503
        else:
            assert response.status_code == 200
            assert verify_export(response.text, pub).ok
    exports = w.db.fetchall("SELECT actor FROM events WHERE kind = 'export'")
    assert [e["actor"] for e in exports] == ["rsouza"]
    denied = [
        c for c in controls_written(w) if c["action"] == "denied" and c["attempted"] == "export"
    ]
    assert [d["actor"] for d in denied] == ["reader", "reader"]
