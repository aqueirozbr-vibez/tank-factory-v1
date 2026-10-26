"""FastAPI application: ``/healthz`` and ``/webhooks/github`` (TRD §6.1).

Handlers validate, record and return. No handler runs an agent or waits on
GitHub (architecture.md · Invariant 20).
"""

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from re import Pattern
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import compile_path

from orchestrator.api.controls import ControlDeps, control_router
from orchestrator.api.limits import RateLimiter, client_ip, identify, limited
from orchestrator.api.limits import error as _error
from orchestrator.api.read import ERRORS, ReadDeps, read_router
from orchestrator.authz import decide
from orchestrator.intake.webhook import GitHubIpRanges, normalise, record_delivery, verify_signature
from orchestrator.models.enums import EventKind
from orchestrator.models.events import NormalisedEvent
from orchestrator.models.settings import Settings
from orchestrator.trace.export import build_export, load_private_key
from orchestrator.trace.retention import purged_logins
from orchestrator.trace.writer import TraceWriter
from orchestrator.version import factory_version
from orchestrator.watchdogs import KillSwitch


@dataclass
class ApiDeps:
    writer: TraceWriter
    settings: Settings
    ip_ranges: GitHubIpRanges
    inbox: asyncio.Queue[NormalisedEvent] = field(default_factory=asyncio.Queue)
    proxy_ok: bool | None = None
    kill_switch: KillSwitch | None = None  # the running orchestrator's switch (unit 40)
    repo_clone: Path | None = None  # spec text for the approvals queue (unit 53)
    limiter: RateLimiter = field(default_factory=RateLimiter)
    controls: ControlDeps | None = None  # the service's doors for the write endpoints (unit 55)


def create_app(deps: ApiDeps) -> FastAPI:
    app = FastAPI(title="factory orchestrator", version="v1")
    api_v1 = APIRouter()
    controls = deps.controls or ControlDeps(
        writer=deps.writer,
        settings=deps.settings,
        limiter=deps.limiter,
        permission_for=_unknown_permission,
        kill_switch=deps.kill_switch,
    )

    served: list[tuple[Pattern[str], str, frozenset[str]]] = []  # filled once the routers are in

    @app.exception_handler(StarletteHTTPException)
    async def routing_error(request: Request, exc: StarletteHTTPException) -> Response:
        """A path or method the router does not know, in the error body everywhere (TRD §6.1)."""
        if exc.status_code == 405:
            path = _route_path(request.scope)
            # The template the router stopped at names the resource; a parameter template that
            # happens to match a literal path (/audit/{task_id} over /audit/queue) is another.
            hits = [(template, m) for regex, template, m in served if regex.match(path)]
            first = hits[0][0] if hits else ""
            allowed = frozenset[str]().union(*(m for template, m in hits if template == first))
            allow = ", ".join(sorted(allowed)) or str((exc.headers or {}).get("Allow", ""))
            response = _error(
                405, "method", f"{request.method} is not a method of this path; Allow: {allow}"
            )
            response.headers["Allow"] = allow
            return response
        if exc.status_code == 404:
            return _error(404, "route", "no such path")
        response = _error(exc.status_code, "http", str(exc.detail))
        for name, value in (exc.headers or {}).items():
            response.headers[name] = value
        return response

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        try:
            installation = deps.writer.get_installation()
            db_ok = True
        except Exception:  # health reports, never raises
            installation, db_ok = None, False
        enabled = bool(installation["factory_enabled"]) if installation else deps.settings.enabled
        status = "ok" if db_ok else "degraded"
        return JSONResponse(
            {
                "status": status,
                "factory_enabled": enabled,
                "db_ok": db_ok,
                "proxy_ok": deps.proxy_ok,
                "last_poll_at": deps.writer.last_poll_at() if db_ok else None,
                "live_processes": deps.writer.count_open_processes() if db_ok else None,
                "version": factory_version(),
            },
            status_code=200 if db_ok else 503,
        )

    @api_v1.get(
        "/tasks/{task_id:path}/export",
        response_class=Response,
        responses={
            **ERRORS,
            200: {
                "description": "signed JSONL, one record per line",
                "content": {"application/x-ndjson": {"schema": {"type": "string"}}},
            },
        },
    )
    async def export_task(task_id: str, request: Request) -> Response:
        """Signed JSONL of one task's events (TRD §4.3, §6.1); recorded as an ``export`` event."""
        caller = identify(request, deps.settings)
        if not caller.known or not caller.actor:
            return _error(
                403,
                "export_caller",
                "export takes the console's service token with X-Factory-Actor",
            )
        permission = await controls.permission_for(caller.actor)
        decision = decide(permission, "export")
        if not decision.allowed:
            # Linked to the task only when it exists; the attempt names it either way.
            linked = task_id if deps.writer.get_task(task_id) is not None else None
            deps.writer.write_event(
                EventKind.CONTROL,
                caller.actor,
                task_id=linked,
                payload={
                    "action": "denied",
                    "attempted": "export",
                    "task_id": task_id,
                    "permission": permission,
                    "reason": decision.reason,
                },
            )
            return _error(403, "export_denied", decision.reason, {"permission": permission})
        if not deps.limiter.allow(caller.key, "export"):
            return limited("export", caller)
        actor = caller.actor
        if deps.writer.get_task(task_id) is None:
            return _error(404, "export_task", f"no task {task_id}")
        key_file = deps.settings.export_key_file
        if key_file is None or not key_file.is_file():
            return _error(503, "export_key", "no export key configured (FACTORY_EXPORT_KEY_FILE)")
        result = build_export(
            deps.writer.db,
            load_private_key(key_file),
            task_id=task_id,
            purged=purged_logins(deps.writer),
        )
        deps.writer.write_event(
            EventKind.EXPORT,
            actor,
            task_id=task_id,
            payload={
                "export": "task",
                "task_id": task_id,
                "from_seq": result.from_seq,
                "to_seq": result.to_seq,
                "records": result.records,
                "head_hash": result.head_hash,
            },
        )
        return Response(result.text(), media_type="application/x-ndjson")

    @app.post("/webhooks/github")
    async def github_webhook(request: Request) -> JSONResponse:
        ip = client_ip(request, deps.settings)
        if not deps.ip_ranges.allows(ip):
            return _error(
                403, "webhook_source", f"source {ip or 'unknown'} is not a GitHub hook address"
            )
        body = await request.body()
        secret = deps.settings.webhook_secret
        if not secret or not verify_signature(
            secret, body, request.headers.get("X-Hub-Signature-256")
        ):
            return _error(401, "webhook_signature", "X-Hub-Signature-256 did not verify")
        event_type = request.headers.get("X-GitHub-Event", "")
        delivery_id = request.headers.get("X-GitHub-Delivery", "")
        if not event_type or not delivery_id:
            return _error(
                400, "webhook_headers", "X-GitHub-Event and X-GitHub-Delivery are required"
            )
        try:
            payload = json.loads(body)
        except ValueError:
            return _error(400, "webhook_body", "body is not JSON")
        if not isinstance(payload, dict):
            return _error(400, "webhook_body", "body is not a JSON object")
        event = normalise(event_type, delivery_id, payload, source="webhook")
        new = record_delivery(deps.writer, event)
        if new and event.kind != "ignored":
            deps.inbox.put_nowait(event)
        return JSONResponse(
            {"accepted": new, "duplicate": not new, "kind": event.kind}, status_code=202
        )

    reads = read_router(
        ReadDeps(deps.writer, deps.settings, deps.limiter, repo_clone=deps.repo_clone)
    )
    writes = control_router(controls)
    app.include_router(api_v1, prefix="/api/v1")
    app.include_router(reads, prefix="/api/v1")
    app.include_router(writes, prefix="/api/v1")
    served.extend(
        _method_table(app, (("/api/v1", api_v1), ("/api/v1", reads), ("/api/v1", writes)))
    )
    return app


def _method_table(
    app: FastAPI, routers: Sequence[tuple[str, APIRouter]]
) -> list[tuple[Pattern[str], str, frozenset[str]]]:
    """Every (path pattern, methods) the app serves, so a 405 can list Allow across routers."""
    entries: list[tuple[str, object]] = [("", route) for route in app.router.routes]
    entries += [(prefix, route) for prefix, router in routers for route in router.routes]
    table: list[tuple[Pattern[str], str, frozenset[str]]] = []
    for prefix, route in entries:
        if isinstance(route, APIRoute):
            regex, template, _ = compile_path(prefix + route.path)
            table.append((regex, template, frozenset(route.methods or ())))
    return table


def _route_path(scope: Mapping[str, Any]) -> str:
    """The path the router matched on: the request path less the mount's root path."""
    path = str(scope.get("path", ""))
    root = str(scope.get("root_path", ""))
    return path[len(root) :] if root and path.startswith(root) else path


async def _unknown_permission(_login: str) -> str | None:
    """Without a GitHub client nobody's permission is known: every write is denied."""
    return None
