"""Unit 18: webhook verification, normalisation, idempotency and the FastAPI app."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestrator.api import ApiDeps, create_app
from orchestrator.intake.webhook import (
    GitHubIpRanges,
    normalise,
    record_delivery,
    sign,
    verify_signature,
)
from orchestrator.models.settings import Settings
from orchestrator.trace import TraceWriter, verify_chain

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "webhooks"
SECRET = "whsec_FAKE_test_secret"
HOOK_RANGES = GitHubIpRanges(("192.30.252.0/22", "140.82.112.0/20"), datetime.now(UTC))


def load(name: str) -> dict[str, object]:
    data: dict[str, object] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


def test_signature_round_trip() -> None:
    body = b'{"zen": "Keep it logically awesome."}'
    header = sign(SECRET, body)
    assert header.startswith("sha256=")
    assert verify_signature(SECRET, body, header)
    assert not verify_signature(SECRET, body + b" ", header)
    assert not verify_signature("other", body, header)
    assert not verify_signature(SECRET, body, None)
    assert not verify_signature(SECRET, body, "sha1=abc")


def test_ip_ranges() -> None:
    ranges = GitHubIpRanges.from_meta({"hooks": ["192.30.252.0/22", "2a0a:a440::/29"]})
    assert ranges.allows("192.30.253.7")
    assert ranges.allows("2a0a:a440::1")
    assert not ranges.allows("10.0.0.1")
    assert not ranges.allows("not-an-ip")
    assert not ranges.allows("127.0.0.1")
    assert GitHubIpRanges(("192.30.252.0/22",), datetime.now(UTC), trust_loopback=True).allows(
        "127.0.0.1"
    )
    old = GitHubIpRanges((), datetime.now(UTC) - timedelta(days=2))
    assert old.is_stale() and not ranges.is_stale()


@pytest.mark.parametrize(
    ("fixture", "event_type", "expected"),
    [
        (
            "issues_labeled.json",
            "issues",
            {
                "kind": "issue_labeled",
                "issue_number": 418,
                "label": "factory:ready",
                "actor_login": "rsouza",
            },
        ),
        (
            "issues_unlabeled.json",
            "issues",
            {
                "kind": "issue_unlabeled",
                "issue_number": 418,
                "label": "factory:needs-human",
                "actor_login": "mlima",
            },
        ),
        (
            "pull_request_merged.json",
            "pull_request",
            {"kind": "pull_request_merged", "pr_number": 431, "branch": "agent/418"},
        ),
        (
            "pull_request_review.json",
            "pull_request_review",
            {
                "kind": "review_submitted",
                "pr_number": 431,
                "review_state": "changes_requested",
                "actor_login": "mlima",
            },
        ),
        (
            "check_run_failure.json",
            "check_run",
            {
                "kind": "check_run_completed",
                "conclusion": "failure",
                "branch": "agent/418",
                "pr_number": 431,
            },
        ),
        (
            "workflow_run_failure.json",
            "workflow_run",
            {
                "kind": "workflow_run_completed",
                "conclusion": "failure",
                "branch": "agent/418",
                "pr_number": 431,
            },
        ),
        (
            "push_main.json",
            "push",
            {
                "kind": "push",
                "branch": "main",
                "changed_paths": (".factory/roster.yaml", "README.md"),
                "actor_login": "mlima",
            },
        ),
        ("issues_opened.json", "issues", {"kind": "ignored", "issue_number": None}),
    ],
)
def test_normalise(fixture: str, event_type: str, expected: dict[str, object]) -> None:
    event = normalise(event_type, f"delivery-{fixture}", load(fixture))
    for key, value in expected.items():
        assert getattr(event, key) == value, key
    assert event.source == "webhook" and event.event_type == event_type
    assert event.idempotency_key == (event_type, f"delivery-{fixture}")


def test_record_delivery_is_idempotent(trace_writer: TraceWriter) -> None:
    event = normalise("issues", "d-1", load("issues_labeled.json"))
    assert record_delivery(trace_writer, event) is True
    assert record_delivery(trace_writer, event) is False
    rows = trace_writer.db.fetchall("SELECT * FROM github_events")
    assert len(rows) == 1 and rows[0]["issue_number"] == 418 and rows[0]["source"] == "webhook"
    events = trace_writer.db.fetchall(
        "SELECT payload_json FROM events WHERE kind = 'webhook' ORDER BY seq"
    )
    flags = [json.loads(e["payload_json"])["duplicate"] for e in events]
    assert flags == [False, True]
    polled = normalise("issues", "d-1", load("issues_labeled.json"), source="poll")
    assert record_delivery(trace_writer, polled) is False  # same id from the poller is one row
    assert verify_chain(trace_writer.db).ok


def _client(
    trace_writer: TraceWriter, *, secret: str | None = SECRET
) -> tuple[TestClient, ApiDeps]:
    deps = ApiDeps(
        writer=trace_writer,
        settings=Settings(webhook_secret=secret, trusted_proxy_header="X-Forwarded-For"),
        ip_ranges=HOOK_RANGES,
    )
    return TestClient(create_app(deps)), deps


def _post(
    client: TestClient,
    fixture: str,
    event_type: str,
    delivery: str,
    *,
    ip: str = "192.30.252.10",
    secret: str = SECRET,
) -> object:
    body = json.dumps(load(fixture)).encode()
    return client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": event_type,
            "X-GitHub-Delivery": delivery,
            "X-Hub-Signature-256": sign(secret, body),
            "X-Forwarded-For": ip,
            "Content-Type": "application/json",
        },
    )


def test_webhook_endpoint(trace_writer: TraceWriter) -> None:
    client, deps = _client(trace_writer)
    first = _post(client, "issues_labeled.json", "issues", "d-100")
    assert first.status_code == 202, first.text  # type: ignore[attr-defined]
    assert first.json() == {"accepted": True, "duplicate": False, "kind": "issue_labeled"}  # type: ignore[attr-defined]
    again = _post(client, "issues_labeled.json", "issues", "d-100")
    assert again.json() == {"accepted": False, "duplicate": True, "kind": "issue_labeled"}  # type: ignore[attr-defined]
    ignored = _post(client, "issues_opened.json", "issues", "d-101")
    assert ignored.json()["kind"] == "ignored"  # type: ignore[attr-defined]
    assert deps.inbox.qsize() == 1  # the duplicate and the ignored event never reach the inbox
    assert deps.inbox.get_nowait().issue_number == 418

    bad_sig = _post(client, "issues_labeled.json", "issues", "d-102", secret="wrong")
    assert bad_sig.status_code == 401  # type: ignore[attr-defined]
    foreign = _post(client, "issues_labeled.json", "issues", "d-103", ip="8.8.8.8")
    assert foreign.status_code == 403  # type: ignore[attr-defined]
    assert trace_writer.db.scalar("SELECT COUNT(*) FROM github_events") == 2

    no_headers = client.post(
        "/webhooks/github",
        content=b"{}",
        headers={"X-Forwarded-For": "192.30.252.10", "X-Hub-Signature-256": sign(SECRET, b"{}")},
    )
    assert no_headers.status_code == 400
    not_json = client.post(
        "/webhooks/github",
        content=b"nope",
        headers={
            "X-Forwarded-For": "192.30.252.10",
            "X-Hub-Signature-256": sign(SECRET, b"nope"),
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": "d-104",
        },
    )
    assert not_json.status_code == 400


def test_webhook_without_secret_configured_rejects_everything(trace_writer: TraceWriter) -> None:
    client, _ = _client(trace_writer, secret=None)
    assert _post(client, "issues_labeled.json", "issues", "d-200").status_code == 401  # type: ignore[attr-defined]


def test_healthz(trace_writer: TraceWriter) -> None:
    client, _ = _client(trace_writer)
    body = client.get("/healthz").json()
    assert body["status"] == "ok" and body["db_ok"] is True and body["factory_enabled"] is True
    assert body["proxy_ok"] is None and body["last_poll_at"] is None
    assert body["live_processes"] == 0
    trace_writer.set_factory_enabled(False)
    trace_writer.set_last_poll("2026-10-24T12:00:00.000+00:00")
    body = client.get("/healthz").json()
    assert body["factory_enabled"] is False
    assert body["last_poll_at"] == "2026-10-24T12:00:00.000+00:00"
