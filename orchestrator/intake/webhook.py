"""Webhook verification and event normalisation (TRD §3.1, §4.5).

A delivery is trusted only with a valid HMAC from a GitHub hook address;
webhooks and the poller produce the same ``NormalisedEvent`` and share one
idempotency key, ``(event_type, github_event_id)``.
"""

import hashlib
import hmac
import ipaddress
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from orchestrator.clock import utc_now
from orchestrator.models.enums import EventKind
from orchestrator.models.events import EventKindName, NormalisedEvent, Source
from orchestrator.trace.writer import TraceWriter

IP_RANGES_TTL = timedelta(days=1)
Payload = Mapping[str, Any]


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """``X-Hub-Signature-256: sha256=<hex>`` checked in constant time."""
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256=") :])


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


@dataclass
class GitHubIpRanges:
    """The ``hooks`` CIDRs from ``GET /meta``, refreshed daily."""

    cidrs: tuple[str, ...]
    fetched_at: datetime
    trust_loopback: bool = False
    _networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = field(
        default_factory=list, repr=False
    )

    def __post_init__(self) -> None:
        self._networks = [ipaddress.ip_network(c, strict=False) for c in self.cidrs]

    @classmethod
    def from_meta(cls, meta: Payload, *, now: datetime | None = None) -> "GitHubIpRanges":
        hooks = meta.get("hooks") or []
        return cls(tuple(str(c) for c in hooks), now or datetime.now(UTC))

    def is_stale(self, now: datetime | None = None) -> bool:
        return (now or datetime.now(UTC)) - self.fetched_at > IP_RANGES_TTL

    def allows(self, ip: str) -> bool:
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            return False
        if self.trust_loopback and address.is_loopback:
            return True
        return any(address in network for network in self._networks)


def _paths(commits: Sequence[Payload]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for commit in commits:
        for key in ("added", "modified", "removed"):
            for path in commit.get(key) or []:
                seen[str(path)] = None
    return tuple(seen)


def normalise(
    event_type: str, delivery_id: str, payload: Payload, *, source: Source = "webhook"
) -> NormalisedEvent:
    action = payload.get("action")
    sender = payload.get("sender") or {}
    actor = str(sender.get("login")) if sender.get("login") else None
    kind: EventKindName = "ignored"
    fields: dict[str, object] = {}

    if event_type == "issues" and action in ("labeled", "unlabeled"):
        issue = payload.get("issue") or {}
        label = payload.get("label") or {}
        kind = "issue_labeled" if action == "labeled" else "issue_unlabeled"
        fields = {"issue_number": issue.get("number"), "label": label.get("name")}
    elif event_type == "pull_request" and action == "closed":
        pr = payload.get("pull_request") or {}
        kind = "pull_request_merged" if pr.get("merged") else "pull_request_closed"
        fields = {
            "pr_number": pr.get("number"),
            "branch": (pr.get("head") or {}).get("ref"),
            "head_sha": (pr.get("head") or {}).get("sha"),
        }
    elif event_type == "pull_request_review" and action == "submitted":
        pr = payload.get("pull_request") or {}
        review = payload.get("review") or {}
        kind = "review_submitted"
        fields = {
            "pr_number": pr.get("number"),
            "branch": (pr.get("head") or {}).get("ref"),
            "review_state": str(review.get("state", "")).lower() or None,
            "actor_login": (review.get("user") or {}).get("login") or actor,
        }
    elif event_type == "check_run" and action == "completed":
        run = payload.get("check_run") or {}
        prs = run.get("pull_requests") or []
        kind = "check_run_completed"
        fields = {
            "conclusion": run.get("conclusion"),
            "head_sha": run.get("head_sha"),
            "branch": (run.get("check_suite") or {}).get("head_branch"),
            "pr_number": prs[0].get("number") if prs else None,
        }
    elif event_type == "workflow_run" and action == "completed":
        run = payload.get("workflow_run") or {}
        prs = run.get("pull_requests") or []
        kind = "workflow_run_completed"
        fields = {
            "conclusion": run.get("conclusion"),
            "head_sha": run.get("head_sha"),
            "branch": run.get("head_branch"),
            "pr_number": prs[0].get("number") if prs else None,
        }
    elif event_type == "push":
        ref = str(payload.get("ref", ""))
        kind = "push"
        fields = {
            "branch": ref.removeprefix("refs/heads/") or None,
            "head_sha": payload.get("after"),
            "changed_paths": _paths(payload.get("commits") or []),
            "actor_login": (payload.get("pusher") or {}).get("name") or actor,
        }

    return NormalisedEvent(
        kind=kind,
        source=source,
        github_event_id=delivery_id,
        event_type=event_type,
        action=str(action) if action is not None else None,
        actor_login=str(fields.pop("actor_login", actor))
        if fields.get("actor_login", actor)
        else None,
        received_at=utc_now(),
        **fields,
    )


def record_delivery(writer: TraceWriter, event: NormalisedEvent) -> bool:
    """Insert the delivery once; write a ``webhook``/``poll`` event either way; return new?"""
    new = writer.record_github_event(
        source=event.source,
        event_type=event.event_type,
        github_event_id=event.github_event_id,
        issue_number=event.issue_number,
    )
    writer.write_event(
        EventKind.WEBHOOK if event.source == "webhook" else EventKind.POLL,
        "orchestrator",
        payload={
            "event_type": event.event_type,
            "action": event.action,
            "kind": event.kind,
            "github_event_id": event.github_event_id,
            "issue_number": event.issue_number,
            "pr_number": event.pr_number,
            "actor": event.actor_login,
            "duplicate": not new,
        },
    )
    return new
