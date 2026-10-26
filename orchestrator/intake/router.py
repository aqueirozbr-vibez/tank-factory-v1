"""Normalised events → the handler that owns them (TRD §4.5 · Router)."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from orchestrator import constants
from orchestrator.models.enums import EventKind
from orchestrator.models.events import NormalisedEvent
from orchestrator.trace.writer import TraceWriter

Handler = Callable[[NormalisedEvent], Awaitable[object]]
CONFIG_PREFIXES = (".factory/",)


@dataclass
class Handlers:
    admit: Handler
    cancel: Handler
    plan_approved: Handler | None = None
    resume: Handler | None = None
    merged: Handler | None = None
    review: Handler | None = None
    ci_failed: Handler | None = None
    push: Handler | None = None


def touches_configuration(paths: tuple[str, ...]) -> bool:
    return any(
        p.startswith(CONFIG_PREFIXES) or p.rsplit("/", 1)[-1] in constants.LOCKFILES for p in paths
    )


class Router:
    def __init__(self, writer: TraceWriter, handlers: Handlers) -> None:
        self.writer = writer
        self.handlers = handlers

    def _pick(self, event: NormalisedEvent) -> tuple[str, Handler | None]:
        h = self.handlers
        if event.kind == "issue_labeled":
            if event.label == constants.LABEL_READY:
                return "admit", h.admit
            if event.label == constants.LABEL_PLAN_APPROVED:
                return "plan_approved", h.plan_approved
            return "ignored", None
        if event.kind == "issue_unlabeled":
            if event.label == constants.LABEL_READY:
                return "cancel", h.cancel
            if event.label == constants.LABEL_NEEDS_HUMAN:
                return "resume", h.resume
            return "ignored", None
        if event.kind == "pull_request_merged":
            return "merged", h.merged
        if event.kind == "review_submitted" and event.review_state == "changes_requested":
            return "review", h.review
        if event.kind in ("check_run_completed", "workflow_run_completed"):
            if event.conclusion in ("failure", "timed_out", "cancelled", "action_required"):
                return "ci_failed", h.ci_failed
            return "ignored", None
        if (
            event.kind == "push"
            and event.branch == "main"
            and touches_configuration(event.changed_paths)
        ):
            return "push", h.push
        return "ignored", None

    async def handle(self, event: NormalisedEvent) -> str:
        route, handler = self._pick(event)
        if handler is None:
            self.writer.write_event(
                EventKind.LOG,
                "orchestrator",
                payload={
                    "router": "unhandled" if route != "ignored" else "ignored",
                    "route": route,
                    "kind": event.kind,
                    "github_event_id": event.github_event_id,
                },
            )
        else:
            await handler(event)
        self.writer.mark_github_event_processed(
            event_type=event.event_type, github_event_id=event.github_event_id
        )
        return route

    async def run(self, inbox: asyncio.Queue[NormalisedEvent], stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                event = await asyncio.wait_for(inbox.get(), 0.5)
            except TimeoutError:
                continue
            try:
                await self.handle(event)
            except Exception as exc:  # the loop survives a bad event; the trace records it
                self.writer.write_event(
                    EventKind.ERROR,
                    "orchestrator",
                    payload={
                        "router": "handler_failed",
                        "kind": event.kind,
                        "error": str(exc)[:300],
                    },
                )
            finally:
                inbox.task_done()
