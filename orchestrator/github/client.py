"""The GitHub REST client the orchestrator uses for every call (PRD §Capability 5).

Writes happen under the App token minted per task; each label and comment
write is recorded as an event naming the identity used.
"""

import asyncio
import base64
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

import httpx

from orchestrator import constants
from orchestrator.errors import FactoryError
from orchestrator.github.auth import API_VERSION, TokenProvider
from orchestrator.models.enums import EventKind
from orchestrator.version import factory_version

JsonDict = dict[str, Any]
RETRY_STATUSES = frozenset({500, 502, 503, 504})
EventRecorder = Callable[[EventKind, JsonDict], None]
Sleep = Callable[[float], Awaitable[None]]


class GitHubApiError(FactoryError):
    code = "github_api_error"

    def __init__(self, status: int, message: str, *, path: str) -> None:
        super().__init__(message, detail={"status": status, "path": path})
        self.status = status
        self.path = path


@dataclass(frozen=True)
class IssueComment:
    id: int
    author_login: str
    author_association: str
    body: str
    created_at: str


class GitHubClient:
    def __init__(
        self,
        *,
        owner: str,
        repo: str,
        tokens: TokenProvider,
        http: httpx.AsyncClient,
        recorder: EventRecorder | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.owner = owner
        self.repo = repo
        self._tokens = tokens
        self._http = http
        self._recorder = recorder
        self._sleep = sleep

    # --- transport ------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        task_id: str,
        json: Mapping[str, object] | None = None,
        params: Mapping[str, str | int] | None = None,
        expected: Sequence[int] = (200,),
    ) -> httpx.Response:
        token = await self._tokens.token_for(task_id)
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": f"tank-factory/{factory_version()}",
        }
        attempt = 0
        while True:
            try:
                response = await self._http.request(
                    method, path, headers=headers, json=json, params=params
                )
            except httpx.TransportError as exc:  # a dropped read or connect: retry, then give up
                if attempt < constants.INFRA_RETRIES:
                    await self._sleep(constants.INFRA_BACKOFF_S[attempt])
                    attempt += 1
                    continue
                raise GitHubApiError(
                    0,
                    f"GitHub {method} {path} failed (transport): {type(exc).__name__}: {exc}",
                    path=path,
                ) from exc
            if response.status_code in expected:
                return response
            rate_limited = response.status_code in (403, 429) and (
                response.headers.get("x-ratelimit-remaining") == "0"
                or "rate limit" in response.text.lower()
            )
            if (
                response.status_code in RETRY_STATUSES or rate_limited
            ) and attempt < constants.INFRA_RETRIES:
                await self._sleep(constants.INFRA_BACKOFF_S[attempt])
                attempt += 1
                continue
            message = response.text[:300]
            with suppress(ValueError):
                message = str(response.json().get("message", message))
            raise GitHubApiError(
                response.status_code,
                f"GitHub {method} {path} failed ({response.status_code}): {message}",
                path=path,
            )

    def _repo(self, suffix: str) -> str:
        return f"/repos/{self.owner}/{self.repo}{suffix}"

    def _record(self, kind: EventKind, payload: JsonDict) -> None:
        if self._recorder is not None:
            self._recorder(kind, {**payload, "identity": "app"})

    # --- issues, comments, labels -------------------------------------------------

    async def get_issue(self, number: int, *, task_id: str) -> JsonDict:
        response = await self._request("GET", self._repo(f"/issues/{number}"), task_id=task_id)
        data: JsonDict = response.json()
        return data

    async def list_issue_comments(self, number: int, *, task_id: str) -> list[IssueComment]:
        comments: list[IssueComment] = []
        page = 1
        while True:
            response = await self._request(
                "GET",
                self._repo(f"/issues/{number}/comments"),
                task_id=task_id,
                params={"per_page": 100, "page": page},
            )
            batch = response.json()
            for raw in batch:
                comments.append(
                    IssueComment(
                        id=int(raw["id"]),
                        author_login=str(raw["user"]["login"]),
                        author_association=str(raw.get("author_association", "NONE")),
                        body=str(raw.get("body") or ""),
                        created_at=str(raw["created_at"]),
                    )
                )
            if len(batch) < 100:
                return comments
            page += 1

    async def create_comment(self, number: int, body: str, *, task_id: str) -> int:
        response = await self._request(
            "POST",
            self._repo(f"/issues/{number}/comments"),
            task_id=task_id,
            json={"body": body},
            expected=(201,),
        )
        comment_id = int(response.json()["id"])
        self._record(
            EventKind.COMMENT_WRITE,
            {"object": f"issue#{number}", "comment_id": comment_id, "chars": len(body)},
        )
        return comment_id

    async def add_labels(self, number: int, labels: Sequence[str], *, task_id: str) -> None:
        await self._request(
            "POST",
            self._repo(f"/issues/{number}/labels"),
            task_id=task_id,
            json={"labels": list(labels)},
        )
        for label in labels:
            self._record(
                EventKind.LABEL_WRITE,
                {"object": f"issue#{number}", "label": label, "action": "add"},
            )

    async def remove_label(self, number: int, label: str, *, task_id: str) -> bool:
        """Remove a label; ``False`` when it was not present (404)."""
        try:
            await self._request(
                "DELETE",
                self._repo(f"/issues/{number}/labels/{label}"),
                task_id=task_id,
                expected=(200, 204),
            )
        except GitHubApiError as exc:
            if exc.status == 404:
                return False
            raise
        self._record(
            EventKind.LABEL_WRITE, {"object": f"issue#{number}", "label": label, "action": "remove"}
        )
        return True

    async def list_labels(self, number: int, *, task_id: str) -> list[str]:
        response = await self._request(
            "GET", self._repo(f"/issues/{number}/labels"), task_id=task_id
        )
        return [str(item["name"]) for item in response.json()]

    # --- people ---------------------------------------------------------------------

    async def collaborator_permission(self, login: str, *, task_id: str) -> str:
        """``admin`` | ``maintain`` | ``write`` | ``triage`` | ``read`` | ``none``."""
        try:
            response = await self._request(
                "GET", self._repo(f"/collaborators/{login}/permission"), task_id=task_id
            )
        except GitHubApiError as exc:
            if exc.status == 404:
                return "none"
            raise
        data = response.json()
        role = data.get("role_name") or data.get("permission") or "none"
        return str(role)

    async def get_meta(self, *, task_id: str) -> JsonDict:
        """``GET /meta``: the hook CIDRs the webhook receiver trusts (TRD §6.1)."""
        response = await self._request("GET", "/meta", task_id=task_id)
        data: JsonDict = response.json()
        return data

    async def get_repository(self, *, task_id: str) -> JsonDict:
        response = await self._request("GET", self._repo(""), task_id=task_id)
        data: JsonDict = response.json()
        return data

    async def get_branch_rules(self, branch: str, *, task_id: str) -> list[JsonDict]:
        """``GET /rules/branches/{branch}``: the active ruleset rules (Metadata read)."""
        response = await self._request(
            "GET", self._repo(f"/rules/branches/{branch}"), task_id=task_id
        )
        data = response.json()
        return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []

    async def get_branch_protection(self, branch: str, *, task_id: str) -> JsonDict | None:
        """Classic protection; ``None`` when absent (404) or unreadable by this App (403)."""
        response = await self._request(
            "GET",
            self._repo(f"/branches/{branch}/protection"),
            task_id=task_id,
            expected=(200, 403, 404),
        )
        if response.status_code != 200:
            return None
        data: JsonDict = response.json()
        return data

    async def create_issue(
        self, *, title: str, body: str, task_id: str, labels: Sequence[str] = ()
    ) -> JsonDict:
        response = await self._request(
            "POST",
            self._repo("/issues"),
            task_id=task_id,
            json={"title": title, "body": body, "labels": list(labels)},
            expected=(201,),
        )
        data: JsonDict = response.json()
        return data

    async def get_user(self, login: str, *, task_id: str) -> JsonDict:
        response = await self._request("GET", f"/users/{login}", task_id=task_id)
        data: JsonDict = response.json()
        return data

    # --- contents and branches ----------------------------------------------------

    async def get_file(self, path: str, *, ref: str, task_id: str) -> str | None:
        try:
            response = await self._request(
                "GET", self._repo(f"/contents/{path}"), task_id=task_id, params={"ref": ref}
            )
        except GitHubApiError as exc:
            if exc.status == 404:
                return None
            raise
        data = response.json()
        if data.get("encoding") == "base64":
            return base64.b64decode(str(data["content"])).decode("utf-8")
        return str(data.get("content", ""))

    async def get_branch_sha(self, branch: str, *, task_id: str) -> str | None:
        try:
            response = await self._request(
                "GET", self._repo(f"/git/ref/heads/{branch}"), task_id=task_id
            )
        except GitHubApiError as exc:
            if exc.status == 404:
                return None
            raise
        return str(response.json()["object"]["sha"])

    async def create_branch(self, branch: str, sha: str, *, task_id: str) -> None:
        await self._request(
            "POST",
            self._repo("/git/refs"),
            task_id=task_id,
            json={"ref": f"refs/heads/{branch}", "sha": sha},
            expected=(201,),
        )

    # --- pull requests ----------------------------------------------------------------

    async def create_pull(
        self, *, title: str, head: str, base: str, body: str, task_id: str
    ) -> JsonDict:
        response = await self._request(
            "POST",
            self._repo("/pulls"),
            task_id=task_id,
            json={"title": title, "head": head, "base": base, "body": body},
            expected=(201,),
        )
        data: JsonDict = response.json()
        return data

    async def get_pull(self, number: int, *, task_id: str) -> JsonDict:
        response = await self._request("GET", self._repo(f"/pulls/{number}"), task_id=task_id)
        data: JsonDict = response.json()
        return data

    async def list_pulls_for_branch(self, head: str, *, task_id: str) -> list[JsonDict]:
        response = await self._request(
            "GET",
            self._repo("/pulls"),
            task_id=task_id,
            params={"head": f"{self.owner}:{head}", "state": "all", "per_page": 20},
        )
        items: list[JsonDict] = response.json()
        return items

    async def request_reviewers(
        self, number: int, reviewers: Sequence[str], *, task_id: str, teams: Sequence[str] = ()
    ) -> None:
        await self._request(
            "POST",
            self._repo(f"/pulls/{number}/requested_reviewers"),
            task_id=task_id,
            json={"reviewers": list(reviewers), "team_reviewers": list(teams)},
            expected=(201,),
        )

    # --- reconciliation reads (unit 19) ----------------------------------------------

    async def list_issue_events(self, *, task_id: str, per_page: int = 100) -> list[JsonDict]:
        """Repository issue events, newest first (labels, assignments, ...)."""
        response = await self._request(
            "GET", self._repo("/issues/events"), task_id=task_id, params={"per_page": per_page}
        )
        items: list[JsonDict] = response.json()
        return items

    async def list_pulls(
        self,
        *,
        task_id: str,
        state: str = "open",
        sort: str = "updated",
        direction: str = "desc",
        per_page: int = 50,
    ) -> list[JsonDict]:
        response = await self._request(
            "GET",
            self._repo("/pulls"),
            task_id=task_id,
            params={"state": state, "sort": sort, "direction": direction, "per_page": per_page},
        )
        items: list[JsonDict] = response.json()
        return items

    async def list_reviews(self, number: int, *, task_id: str) -> list[JsonDict]:
        response = await self._request(
            "GET", self._repo(f"/pulls/{number}/reviews"), task_id=task_id, params={"per_page": 100}
        )
        items: list[JsonDict] = response.json()
        return items

    async def list_check_runs(self, sha: str, *, task_id: str) -> list[JsonDict]:
        response = await self._request(
            "GET",
            self._repo(f"/commits/{sha}/check-runs"),
            task_id=task_id,
            params={"per_page": 100},
        )
        runs: list[JsonDict] = response.json().get("check_runs", [])
        return runs

    async def compare_files(self, base: str, head: str, *, task_id: str) -> list[str]:
        response = await self._request(
            "GET", self._repo(f"/compare/{base}...{head}"), task_id=task_id
        )
        files = response.json().get("files", [])
        return [str(f["filename"]) for f in files]
