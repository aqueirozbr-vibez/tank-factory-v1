"""GitHub App identity: the App JWT and per-task installation tokens (TRD §3.4).

The private key never leaves this module's caller; the installation token
lives in orchestrator memory, is minted per task, and is never written to the
trace or passed to an agent (architecture.md · Invariant 5).
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import httpx
import jwt

from orchestrator.errors import FactoryError

API_VERSION = "2022-11-28"
TOKEN_PERMISSIONS = {"contents": "write", "issues": "write", "pull_requests": "write"}
JWT_BACKDATE_S = 60
JWT_LIFETIME_S = 9 * 60
TOKEN_REFRESH_MARGIN_S = 5 * 60


class GitHubAuthError(FactoryError):
    code = "github_auth_error"


class TokenProvider(Protocol):
    async def token_for(self, task_id: str) -> str: ...


def app_jwt(app_id: str, private_key_pem: str, *, now: float | None = None) -> str:
    moment = int(now if now is not None else time.time())
    payload = {"iat": moment - JWT_BACKDATE_S, "exp": moment + JWT_LIFETIME_S, "iss": app_id}
    return jwt.encode(payload, private_key_pem, algorithm="RS256")


@dataclass
class _CachedToken:
    token: str
    expires_at: float


class InstallationTokenMinter:
    """Mints one installation token per task and caches it until near expiry."""

    def __init__(
        self,
        *,
        app_id: str,
        private_key_pem: str,
        installation_id: int,
        http: httpx.AsyncClient,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._app_id = app_id
        self._pem = private_key_pem
        self._installation_id = installation_id
        self._http = http
        self._clock = clock
        self._cache: dict[str, _CachedToken] = {}
        self.mints = 0

    async def token_for(self, task_id: str) -> str:
        cached = self._cache.get(task_id)
        now = self._clock()
        if cached is not None and cached.expires_at - TOKEN_REFRESH_MARGIN_S > now:
            return cached.token
        response = await self._http.post(
            f"/app/installations/{self._installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {app_jwt(self._app_id, self._pem, now=now)}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
            },
            json={"permissions": TOKEN_PERMISSIONS},
        )
        if response.status_code != 201:
            raise GitHubAuthError(
                f"installation token refused ({response.status_code}): {response.text[:200]}"
            )
        body = response.json()
        expires = datetime.fromisoformat(str(body["expires_at"]).replace("Z", "+00:00"))
        self._cache[task_id] = _CachedToken(
            token=str(body["token"]), expires_at=expires.astimezone(UTC).timestamp()
        )
        self.mints += 1
        return self._cache[task_id].token

    def forget(self, task_id: str) -> None:
        self._cache.pop(task_id, None)
