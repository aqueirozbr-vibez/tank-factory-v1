"""Intake: GitHub events in, admitted tasks out (PRD §Capability 1)."""

from orchestrator.intake.admission import Admission, AdmissionResult, Cancellation
from orchestrator.intake.ci import CiReentry, ReentryResult, pending_reentry
from orchestrator.intake.issue_fetcher import TaggedIssue, fetch_issue
from orchestrator.intake.poller import Poller, PollReport
from orchestrator.intake.resume import Resume, ResumeResult
from orchestrator.intake.router import Handlers, Router
from orchestrator.intake.scheduler import Scheduler
from orchestrator.intake.webhook import (
    GitHubIpRanges,
    normalise,
    record_delivery,
    verify_signature,
)

__all__ = [
    "Admission",
    "AdmissionResult",
    "Cancellation",
    "CiReentry",
    "GitHubIpRanges",
    "Handlers",
    "PollReport",
    "Poller",
    "ReentryResult",
    "Resume",
    "ResumeResult",
    "Router",
    "Scheduler",
    "TaggedIssue",
    "fetch_issue",
    "normalise",
    "pending_reentry",
    "record_delivery",
    "verify_signature",
]
