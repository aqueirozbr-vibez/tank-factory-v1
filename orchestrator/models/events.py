"""Normalised GitHub events: webhooks and polls produce the same shape (TRD §4.5)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EventKindName = Literal[
    "issue_labeled",
    "issue_unlabeled",
    "pull_request_merged",
    "pull_request_closed",
    "review_submitted",
    "check_run_completed",
    "workflow_run_completed",
    "push",
    "ignored",
]
Source = Literal["webhook", "poll"]


class NormalisedEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: EventKindName
    source: Source
    github_event_id: str = Field(min_length=1, description="delivery id or reconstructed id")
    event_type: str = Field(min_length=1, description="the X-GitHub-Event name")
    action: str | None = None
    issue_number: int | None = None
    pr_number: int | None = None
    actor_login: str | None = None
    label: str | None = None
    branch: str | None = None
    head_sha: str | None = None
    conclusion: str | None = None
    review_state: str | None = None
    changed_paths: tuple[str, ...] = ()
    received_at: str
    occurred_at: str | None = None  # GitHub's own timestamp when a poll reconstructs it

    @property
    def idempotency_key(self) -> tuple[str, str]:
        return (self.event_type, self.github_event_id)
