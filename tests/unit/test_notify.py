"""Unit 50: Slack notices for a plan review waiting and a stuck run; notification only."""

import asyncio
import contextlib
import json
from datetime import timedelta
from pathlib import Path

import httpx

from orchestrator import constants
from orchestrator.models.enums import RiskClass
from orchestrator.notify.slack import Notice, SlackNotifier, plan_review_notice, stuck_run_notice
from orchestrator.phases.approval import PlanApprovals
from orchestrator.trace import TraceWriter
from orchestrator.watchdogs import StuckProcess, Watchdog
from tests.unit.test_admission import FakeGitHub
from tests.unit.test_approval import approval_event, seed, start_waiter
from tests.unit.test_budget_watchdogs import _process
from tests.unit.test_spec_gate import TASK

SNAPSHOTS = Path(__file__).resolve().parents[2] / "fixtures" / "notify"
URL = "https://hooks.slack.com/services/T000/B000/secret-token"
SPEC_REL = "specs/418-invoice-due-date.md"


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[Notice] = []

    async def send(self, notice: Notice) -> bool:
        self.sent.append(notice)
        return True


def plan_notice() -> Notice:
    return plan_review_notice(
        task_id=TASK,
        issue_number=418,
        title="Invoice due date",
        risk_class="risk:governed",
        spec_rel=SPEC_REL,
        governed_path="src/billing/",
        owners=("mlima", "rsouza"),
    )


def stuck_notice() -> Notice:
    return stuck_run_notice(
        task_id=TASK,
        issue_number=418,
        title="Invoice due date",
        phase_name="claude",
        idle_s=16 * 60 + 3,
        threshold_s=constants.STUCK_AFTER_S,
        last_event_at="2026-10-24T12:00:00.000+00:00",
    )


def test_payloads_match_their_snapshots() -> None:
    for notice in (plan_notice(), stuck_notice()):
        expected = json.loads((SNAPSHOTS / f"{notice.kind}.json").read_text(encoding="utf-8"))
        assert notice.payload() == expected, notice.kind
    payload = plan_notice().payload()
    blocks = payload["blocks"]
    assert isinstance(blocks, list) and [b["type"] for b in blocks] == ["section", "context"]
    text = json.dumps(payload)
    assert constants.LABEL_PLAN_APPROVED in text and "mlima, rsouza" in text
    assert "actions" not in text and "button" not in text  # nothing to click: notification only
    plain = stuck_run_notice(
        task_id=TASK,
        issue_number=12,
        title=None,
        phase_name="codex",
        idle_s=901,
        threshold_s=900,
        last_event_at="x",
    )
    assert plain.title == "Stuck run: #12" and plain.fields["Idle"] == "15 min (flagged after 15)"


async def test_send_logs_each_outcome_and_never_leaks_the_url(trace_writer: TraceWriter) -> None:
    seed(trace_writer, RiskClass.HIGH)  # the notice's task exists, as it always does in service
    replies: list[httpx.Response | Exception] = [
        httpx.Response(200, text="ok"),
        httpx.Response(500, text="server_error"),
        httpx.ConnectError("refused"),
    ]
    bodies: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    notifier = SlackNotifier(
        URL, trace_writer, http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    assert await notifier.send(plan_notice()) is True
    assert await notifier.send(stuck_notice()) is False
    assert await notifier.send(stuck_notice()) is False  # a connection error does not raise
    await notifier.aclose()
    assert bodies[0] == plan_notice().payload() and len(bodies) == 3
    rows = trace_writer.db.fetchall(
        "SELECT payload_json FROM events WHERE kind = 'log' AND payload_json LIKE ? ORDER BY seq",
        ('%"notify":"slack"%',),
    )
    logs = [json.loads(r["payload_json"]) for r in rows]
    assert [(e["kind"], e["ok"], e["status"], e["error"]) for e in logs] == [
        ("plan_review_waiting", True, 200, None),
        ("stuck_run", False, 500, "server_error"),
        ("stuck_run", False, None, "ConnectError"),
    ]
    leaked = trace_writer.db.scalar(
        "SELECT COUNT(*) FROM events WHERE payload_json LIKE '%hooks.slack.com%'"
        " OR payload_json LIKE '%secret-token%'"
    )
    assert leaked == 0


async def test_waiter_notifies_once_and_only_the_label_ends_the_wait(
    trace_writer: TraceWriter, product: Path
) -> None:
    run_id = seed(trace_writer, RiskClass.HIGH)
    github = FakeGitHub()
    notifier = FakeNotifier()
    approvals = PlanApprovals(
        writer=trace_writer, github=github, repo_clone=product, notifier=notifier
    )
    first = await start_waiter(trace_writer, github, approvals, RiskClass.HIGH, run_id)
    assert [n.kind for n in notifier.sent] == ["plan_review_waiting"]
    assert notifier.sent[0].title == "Plan review waiting: #418 Invoice due date"
    assert notifier.sent[0].fields["Approver"] == "a user with write access"
    await asyncio.sleep(0.05)
    assert approvals.waiting(TASK) and not first.done()  # Slack answered; the wait goes on

    first.cancel()  # the orchestrator restarts mid-wait
    with contextlib.suppress(asyncio.CancelledError):
        await first
    assert not approvals.waiting(TASK)
    second = await start_waiter(
        trace_writer,
        github,
        approvals,
        RiskClass.HIGH,
        run_id,
        seq_start=trace_writer.last_phase_seq(run_id),
    )
    assert len(notifier.sent) == 1 and len(github.comments()) == 1  # announced before: no repost

    counted = await approvals.on_label(approval_event("rsouza", delivery="d2"))
    outcome = await asyncio.wait_for(second, 5)
    assert counted.kind == "approved" and outcome.approved and outcome.actor == "rsouza"


async def test_watchdog_loop_calls_the_hook_once_per_stuck_process(
    trace_writer: TraceWriter,
) -> None:
    stuck_id = _process(trace_writer, idle=timedelta(minutes=16))
    _process(trace_writer, idle=timedelta(minutes=2))
    seen: list[StuckProcess] = []

    async def on_stuck(process: StuckProcess) -> None:
        seen.append(process)

    watchdog = Watchdog(trace_writer, interval_s=0.01, on_stuck=on_stuck)
    stop = asyncio.Event()
    loop = asyncio.create_task(watchdog.run(stop))
    await asyncio.sleep(0.1)
    stop.set()
    await asyncio.wait_for(loop, 2)
    assert [p.id for p in seen] == [stuck_id]
    task = trace_writer.get_task(seen[0].task_id)
    assert task is not None
    notice = stuck_run_notice(
        task_id=seen[0].task_id,
        issue_number=int(task["issue_number"]),
        title=task["title"],
        phase_name=seen[0].name,
        idle_s=int(seen[0].idle_s),
        threshold_s=constants.STUCK_AFTER_S,
        last_event_at=seen[0].last_event_at,
    )
    assert notice.title == f"Stuck run: #{task['issue_number']}"
    assert notice.fields["Process"] == "claude" and notice.fields["Idle"].startswith("16 min")
