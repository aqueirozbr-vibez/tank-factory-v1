"""A dropped connection to GitHub is retried with the infra backoff, then becomes an API error."""

import httpx
import pytest

from orchestrator import constants
from orchestrator.github import GitHubApiError, GitHubClient
from tests.unit.test_github_client import TASK, StaticTokens


def client_with(handler: object) -> tuple[GitHubClient, list[float]]:
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
        base_url="https://api.github.com",
    )
    client = GitHubClient(
        owner="acme", repo="atlas-api", tokens=StaticTokens(), http=http, sleep=sleep
    )
    return client, sleeps


async def test_a_dropped_read_is_retried_then_served() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadError("connection reset", request=request)
        return httpx.Response(200, json={"number": 418, "title": "Invoice due date"})

    client, sleeps = client_with(handler)
    issue = await client.get_issue(418, task_id=TASK)
    assert issue["number"] == 418 and len(calls) == 2
    assert sleeps == [constants.INFRA_BACKOFF_S[0]]


async def test_a_dead_connection_becomes_an_api_error_after_the_retries() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ConnectError("no route", request=request)

    client, sleeps = client_with(handler)
    with pytest.raises(GitHubApiError) as caught:
        await client.get_issue(418, task_id=TASK)
    assert caught.value.status == 0 and "transport" in str(caught.value)
    assert "ConnectError" in str(caught.value)
    assert len(calls) == constants.INFRA_RETRIES + 1
    assert sleeps == list(constants.INFRA_BACKOFF_S)
