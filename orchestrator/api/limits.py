"""Callers, the error body and the rate limits of the API (TRD §6.1, §6.2; code-standards · API).

A caller is the console (service token plus ``X-Factory-Actor``), a
loopback operator (the CLI, until the console lands), or nobody. The
limits are the TRD's, as token buckets per login (or per address when
there is no login); a limited request answers 429 and names the rule.
"""

import hmac
import ipaddress
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from fastapi import Request
from fastapi.responses import JSONResponse

from orchestrator.models.api import ErrorBody
from orchestrator.models.settings import Settings

Via = Literal["service_token", "loopback", "anonymous"]
SERVICE_TOKEN_HEADER = "X-Factory-Service-Token"
ACTOR_HEADER = "X-Factory-Actor"

# rule → (requests, per seconds); the TRD §6.1 column "Rate limit"
RULES: dict[str, tuple[int, float]] = {
    "read": (300, 60.0),
    "count": (600, 60.0),
    "audit": (60, 60.0),
    "export": (10, 3600.0),
    "write": (10, 60.0),
    "kill_switch": (1, 60.0),
    "eval_run": (2, 3600.0),
}
STREAMS_PER_LOGIN = 5


@dataclass(frozen=True)
class Caller:
    actor: str | None
    via: Via
    address: str

    @property
    def key(self) -> str:
        """What a limit is counted against: the login, else the address."""
        return self.actor or self.address or "unknown"

    @property
    def known(self) -> bool:
        return self.via != "anonymous"


def is_loopback(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


def client_ip(request: Request, settings: Settings) -> str:
    header = settings.trusted_proxy_header
    if header:
        forwarded = request.headers.get(header)
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def identify(request: Request, settings: Settings) -> Caller:
    """The console's service token names the actor; loopback stands in for it until then."""
    address = client_ip(request, settings)
    actor = request.headers.get(ACTOR_HEADER, "").strip() or None
    token = request.headers.get(SERVICE_TOKEN_HEADER, "")
    if settings.service_token and token and hmac.compare_digest(token, settings.service_token):
        return Caller(actor, "service_token", address)
    if is_loopback(address):
        return Caller(actor, "loopback", address)
    return Caller(None, "anonymous", address)


def error(status: int, code: str, message: str, detail: object | None = None) -> JSONResponse:
    body = ErrorBody(code=code, message=message, detail=detail, trace_id=uuid.uuid4().hex[:16])
    return JSONResponse(body.model_dump(by_alias=True, exclude_none=True), status_code=status)


class RateLimiter:
    """Token buckets per (caller key, rule); a bucket holds the rule's full count at rest."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._buckets: dict[tuple[str, str], tuple[float, float]] = {}
        self._streams: dict[str, int] = {}

    def allow(self, key: str, rule: str) -> bool:
        limit, per = RULES[rule]
        now = self.clock()
        tokens, last = self._buckets.get((key, rule), (float(limit), now))
        tokens = min(float(limit), tokens + (now - last) * limit / per)
        if tokens < 1.0:
            self._buckets[(key, rule)] = (tokens, now)
            return False
        self._buckets[(key, rule)] = (tokens - 1.0, now)
        return True

    def open_stream(self, key: str) -> bool:
        if self._streams.get(key, 0) >= STREAMS_PER_LOGIN:
            return False
        self._streams[key] = self._streams.get(key, 0) + 1
        return True

    def close_stream(self, key: str) -> None:
        left = self._streams.get(key, 0) - 1
        if left <= 0:
            self._streams.pop(key, None)
        else:
            self._streams[key] = left

    def streams(self, key: str) -> int:
        return self._streams.get(key, 0)


def limited(rule: str, caller: Caller) -> JSONResponse:
    limit, per = RULES[rule]
    window = f"{int(per)} s" if per < 3600 else f"{int(per // 3600)} h"
    return error(
        429,
        "rate_limited",
        f"rate limit {rule}: {limit} per {window} per {'login' if caller.actor else 'address'}",
        {"rule": rule, "limit": limit, "per_s": per},
    )


__all__ = [
    "ACTOR_HEADER",
    "RULES",
    "SERVICE_TOKEN_HEADER",
    "STREAMS_PER_LOGIN",
    "Caller",
    "RateLimiter",
    "client_ip",
    "error",
    "identify",
    "is_loopback",
    "limited",
]
