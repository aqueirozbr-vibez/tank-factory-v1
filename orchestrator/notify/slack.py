"""Slack incoming-webhook notices: plan review waiting and stuck run (PRD §Capability 2, 5).

A notice is a notification and nothing else: no button, no reply path, no
state. The approval is the ``factory:plan-approved`` label on GitHub and
the stuck flag is the ``log`` event the watchdog wrote; Slack hears about
both when a webhook URL is configured. The URL carries a secret, so it
never reaches the trace, a log line or an exception message: a failed post
is recorded by status and error class only.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx

from orchestrator import constants
from orchestrator.models.enums import EventKind
from orchestrator.trace.writer import TraceWriter

NoticeKind = Literal["plan_review_waiting", "stuck_run"]
PLAN_REVIEW_FOOTER = (
    f"Notification only. The approval is the `{constants.LABEL_PLAN_APPROVED}` label on GitHub."
)
STUCK_RUN_FOOTER = "Notification only. The phase timeout governs the kill."
POST_TIMEOUT_S = 10.0
BODY_EXCERPT = 80  # of Slack's reply on a failure; never the URL


@dataclass(frozen=True)
class Notice:
    kind: NoticeKind
    task_id: str
    title: str
    fields: Mapping[str, str]
    footer: str

    def payload(self) -> dict[str, object]:
        """The webhook body: plain ``text`` plus one section and one context block."""
        lines = "\n".join(f"*{name}:* {value}" for name, value in self.fields.items())
        return {
            "text": self.title,
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*{self.title}*\n{lines}"},
                },
                {"type": "context", "elements": [{"type": "mrkdwn", "text": self.footer}]},
            ],
        }


def plan_review_notice(
    *,
    task_id: str,
    issue_number: int,
    title: str,
    risk_class: str,
    spec_rel: str,
    governed_path: str | None,
    owners: Sequence[str],
) -> Notice:
    if governed_path is not None:
        named = ", ".join(owners) if owners else "nobody is listed in CODEOWNERS"
        approver = f"a CODEOWNER of `{governed_path}` ({named})"
    else:
        approver = "a user with write access"
    return Notice(
        "plan_review_waiting",
        task_id,
        f"Plan review waiting: #{issue_number} {title}",
        {
            "Task": task_id,
            "Risk": risk_class,
            "Spec": spec_rel,
            "Approver": approver,
            "Label to add": f"`{constants.LABEL_PLAN_APPROVED}` on the issue",
        },
        PLAN_REVIEW_FOOTER,
    )


def stuck_run_notice(
    *,
    task_id: str,
    issue_number: int | None,
    title: str | None,
    phase_name: str,
    idle_s: int,
    threshold_s: int,
    last_event_at: str,
) -> Notice:
    if issue_number is None:
        subject = task_id
    elif title:
        subject = f"#{issue_number} {title}"
    else:
        subject = f"#{issue_number}"
    return Notice(
        "stuck_run",
        task_id,
        f"Stuck run: {subject}",
        {
            "Task": task_id,
            "Process": phase_name,
            "Idle": f"{idle_s // 60} min (flagged after {threshold_s // 60})",
            "Last event": last_event_at,
        },
        STUCK_RUN_FOOTER,
    )


class NotifierPort(Protocol):
    async def send(self, notice: Notice) -> bool: ...


class SlackNotifier:
    """Posts each notice once to the incoming webhook; a failure is logged, never raised."""

    def __init__(
        self,
        webhook_url: str,
        writer: TraceWriter,
        *,
        http: httpx.AsyncClient | None = None,
        timeout_s: float = POST_TIMEOUT_S,
    ) -> None:
        self._url = webhook_url
        self.writer = writer
        self._http = http or httpx.AsyncClient(timeout=timeout_s)
        self.timeout_s = timeout_s

    async def send(self, notice: Notice) -> bool:
        status: int | None = None
        error: str | None = None
        try:
            response = await self._http.post(
                self._url, json=notice.payload(), timeout=self.timeout_s
            )
        except httpx.HTTPError as exc:
            error = type(exc).__name__  # the message would carry the URL
        else:
            status = response.status_code
            if status != 200:
                error = response.text[:BODY_EXCERPT]
        ok = status == 200
        self.writer.write_event(
            EventKind.LOG,
            "orchestrator",
            task_id=notice.task_id,
            payload={
                "notify": "slack",
                "kind": notice.kind,
                "ok": ok,
                "status": status,
                "error": error,
            },
        )
        return ok

    async def aclose(self) -> None:
        await self._http.aclose()


__all__ = [
    "PLAN_REVIEW_FOOTER",
    "STUCK_RUN_FOOTER",
    "Notice",
    "NotifierPort",
    "SlackNotifier",
    "plan_review_notice",
    "stuck_run_notice",
]
