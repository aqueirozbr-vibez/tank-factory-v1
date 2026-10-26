"""Unit 17: the GitHub client against recorded responses, no network."""

import base64
import json
from collections.abc import Callable
from pathlib import Path

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from orchestrator.github import (
    GitHubApiError,
    GitHubClient,
    InstallationTokenMinter,
    app_jwt,
    owners_for,
    parse_codeowners,
)
from orchestrator.github.auth import GitHubAuthError
from orchestrator.models.enums import EventKind

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "github"
TASK = "acme/atlas-api#418"


@pytest.fixture(scope="module")
def keypair() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return private_pem, public_pem


def fixture(name: str) -> dict[str, object]:
    data: dict[str, object] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


class Recorded:
    """A scripted transport that remembers every request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.responders: dict[tuple[str, str], Callable[[httpx.Request], httpx.Response]] = {}
        self.sequence: dict[tuple[str, str], list[httpx.Response]] = {}

    def on(self, method: str, path: str, status: int, body: object = None) -> None:
        self.responders[(method, path)] = lambda _req: httpx.Response(status, json=body)

    def queue(self, method: str, path: str, *responses: httpx.Response) -> None:
        self.sequence[(method, path)] = list(responses)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (request.method, request.url.path)
        if self.sequence.get(key):
            return self.sequence[key].pop(0)
        responder = self.responders.get(key)
        if responder is None:
            return httpx.Response(404, json={"message": f"no fixture for {key}"})
        return responder(request)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(self.handler), base_url="https://api.github.com"
        )


class StaticTokens:
    def __init__(self, token: str = "ghs_FAKEtoken") -> None:
        self.token = token
        self.calls: list[str] = []

    async def token_for(self, task_id: str) -> str:
        self.calls.append(task_id)
        return self.token


def make_client(
    recorded: Recorded, events: list[tuple[EventKind, dict[str, object]]]
) -> GitHubClient:
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    client = GitHubClient(
        owner="acme",
        repo="atlas-api",
        tokens=StaticTokens(),
        http=recorded.client(),
        recorder=lambda kind, payload: events.append((kind, payload)),
        sleep=sleep,
    )
    client.sleeps = sleeps  # type: ignore[attr-defined]
    return client


# --- auth ---------------------------------------------------------------------------


def test_app_jwt_claims(keypair: tuple[str, str]) -> None:
    private_pem, public_pem = keypair
    token = app_jwt("12345", private_pem, now=1_700_000_000)
    claims = jwt.decode(token, public_pem, algorithms=["RS256"], options={"verify_exp": False})
    assert claims == {"iat": 1_700_000_000 - 60, "exp": 1_700_000_000 + 540, "iss": "12345"}


async def test_installation_token_is_minted_per_task_and_refreshed(
    keypair: tuple[str, str],
) -> None:
    private_pem, public_pem = keypair
    recorded = Recorded()
    recorded.on("POST", "/app/installations/77/access_tokens", 201, fixture("access_token.json"))
    now = {"t": 1_700_000_000.0}
    minter = InstallationTokenMinter(
        app_id="12345",
        private_key_pem=private_pem,
        installation_id=77,
        http=recorded.client(),
        clock=lambda: now["t"],
    )
    first = await minter.token_for(TASK)
    second = await minter.token_for(TASK)
    other = await minter.token_for("acme/atlas-api#419")
    assert first == second == other == "ghs_FAKE_installation_token"
    assert minter.mints == 2
    request = recorded.requests[0]
    assert request.headers["authorization"].startswith("Bearer ")
    bearer = request.headers["authorization"].split(" ", 1)[1]
    assert (
        jwt.decode(bearer, public_pem, algorithms=["RS256"], options={"verify_exp": False})["iss"]
        == "12345"
    )
    assert json.loads(request.content)["permissions"] == {
        "contents": "write",
        "issues": "write",
        "pull_requests": "write",
    }
    now["t"] = (
        1_700_000_000.0 + 3600
    )  # one hour later: past the fixture's expiry minus the refresh margin
    await minter.token_for(TASK)
    assert minter.mints == 3

    recorded.on("POST", "/app/installations/77/access_tokens", 401, {"message": "bad credentials"})
    minter.forget(TASK)
    with pytest.raises(GitHubAuthError, match="401"):
        await minter.token_for(TASK)


# --- client -------------------------------------------------------------------------


async def test_issue_comments_and_headers() -> None:
    recorded = Recorded()
    recorded.on("GET", "/repos/acme/atlas-api/issues/418", 200, fixture("issue_418.json"))
    recorded.on(
        "GET", "/repos/acme/atlas-api/issues/418/comments", 200, fixture("comments_418.json")
    )
    events: list[tuple[EventKind, dict[str, object]]] = []
    client = make_client(recorded, events)
    issue = await client.get_issue(418, task_id=TASK)
    assert issue["title"] == "Invoice due date"
    assert "## Holdout" in str(issue["body"])
    comments = await client.list_issue_comments(418, task_id=TASK)
    assert [(c.author_login, c.author_association) for c in comments] == [
        ("rsouza", "MEMBER"),
        ("drive-by", "NONE"),
    ]
    headers = recorded.requests[0].headers
    assert headers["authorization"] == "Bearer ghs_FAKEtoken"
    assert headers["accept"] == "application/vnd.github+json"
    assert headers["x-github-api-version"] == "2022-11-28"
    assert headers["user-agent"].startswith("tank-factory/")
    assert events == []


async def test_writes_record_events_under_the_app_identity() -> None:
    recorded = Recorded()
    recorded.on("POST", "/repos/acme/atlas-api/issues/418/comments", 201, {"id": 9001})
    recorded.on(
        "POST", "/repos/acme/atlas-api/issues/418/labels", 200, [{"name": "factory:plan-review"}]
    )
    recorded.on("DELETE", "/repos/acme/atlas-api/issues/418/labels/factory:ready", 200, [])
    recorded.on(
        "DELETE",
        "/repos/acme/atlas-api/issues/418/labels/missing",
        404,
        {"message": "Label does not exist"},
    )
    events: list[tuple[EventKind, dict[str, object]]] = []
    client = make_client(recorded, events)
    assert await client.create_comment(418, "Spec posted for review.", task_id=TASK) == 9001
    await client.add_labels(418, ["factory:plan-review", "risk:high"], task_id=TASK)
    assert await client.remove_label(418, "factory:ready", task_id=TASK) is True
    assert await client.remove_label(418, "missing", task_id=TASK) is False
    kinds = [k for k, _ in events]
    assert kinds == [
        EventKind.COMMENT_WRITE,
        EventKind.LABEL_WRITE,
        EventKind.LABEL_WRITE,
        EventKind.LABEL_WRITE,
    ]
    assert all(p["identity"] == "app" for _, p in events)
    assert events[1][1] == {
        "object": "issue#418",
        "label": "factory:plan-review",
        "action": "add",
        "identity": "app",
    }
    assert events[3][1]["action"] == "remove"
    body = json.loads(recorded.requests[1].content)
    assert body == {"labels": ["factory:plan-review", "risk:high"]}


async def test_permission_contents_branches_and_pulls() -> None:
    recorded = Recorded()
    recorded.on(
        "GET",
        "/repos/acme/atlas-api/collaborators/rsouza/permission",
        200,
        fixture("permission_write.json"),
    )
    recorded.on(
        "GET",
        "/repos/acme/atlas-api/collaborators/stranger/permission",
        404,
        {"message": "Not Found"},
    )
    content = base64.b64encode(b"* @acme/platform\n").decode()
    recorded.on(
        "GET",
        "/repos/acme/atlas-api/contents/CODEOWNERS",
        200,
        {"encoding": "base64", "content": content},
    )
    recorded.on("GET", "/repos/acme/atlas-api/contents/missing.md", 404, {"message": "Not Found"})
    recorded.on(
        "GET", "/repos/acme/atlas-api/git/ref/heads/main", 200, {"object": {"sha": "a" * 40}}
    )
    recorded.on(
        "GET", "/repos/acme/atlas-api/git/ref/heads/agent/418", 404, {"message": "Not Found"}
    )
    recorded.on("POST", "/repos/acme/atlas-api/git/refs", 201, {"ref": "refs/heads/agent/418"})
    recorded.on("POST", "/repos/acme/atlas-api/pulls", 201, fixture("pull_created.json"))
    recorded.on("GET", "/repos/acme/atlas-api/pulls", 200, [fixture("pull_created.json")])
    recorded.on("POST", "/repos/acme/atlas-api/pulls/431/requested_reviewers", 201, {})
    events: list[tuple[EventKind, dict[str, object]]] = []
    client = make_client(recorded, events)

    assert await client.collaborator_permission("rsouza", task_id=TASK) == "write"
    assert await client.collaborator_permission("stranger", task_id=TASK) == "none"
    assert await client.get_file("CODEOWNERS", ref="main", task_id=TASK) == "* @acme/platform\n"
    assert await client.get_file("missing.md", ref="main", task_id=TASK) is None
    assert recorded.requests[2].url.params["ref"] == "main"
    assert await client.get_branch_sha("main", task_id=TASK) == "a" * 40
    assert await client.get_branch_sha("agent/418", task_id=TASK) is None
    await client.create_branch("agent/418", "a" * 40, task_id=TASK)
    assert json.loads(recorded.requests[-1].content) == {
        "ref": "refs/heads/agent/418",
        "sha": "a" * 40,
    }

    pull = await client.create_pull(
        title="feat(billing): invoice due_date (#418)",
        head="agent/418",
        base="main",
        body="generated from the trace",
        task_id=TASK,
    )
    assert pull["number"] == 431
    assert json.loads(recorded.requests[-1].content)["head"] == "agent/418"
    pulls = await client.list_pulls_for_branch("agent/418", task_id=TASK)
    assert [p["number"] for p in pulls] == [431]
    assert recorded.requests[-1].url.params["head"] == "acme:agent/418"
    await client.request_reviewers(431, ["mlima"], task_id=TASK, teams=["platform"])
    assert json.loads(recorded.requests[-1].content) == {
        "reviewers": ["mlima"],
        "team_reviewers": ["platform"],
    }
    assert events == []  # reads, branches and PRs record no label or comment events here


async def test_retries_on_5xx_and_rate_limit_then_raises_on_4xx() -> None:
    recorded = Recorded()
    recorded.queue(
        "GET",
        "/repos/acme/atlas-api/issues/1",
        httpx.Response(502, json={"message": "bad gateway"}),
        httpx.Response(
            403, json={"message": "API rate limit exceeded"}, headers={"x-ratelimit-remaining": "0"}
        ),
        httpx.Response(200, json={"number": 1, "title": "ok"}),
    )
    recorded.on("GET", "/repos/acme/atlas-api/issues/2", 422, {"message": "Validation Failed"})
    recorded.queue(
        "GET",
        "/repos/acme/atlas-api/issues/3",
        *[httpx.Response(503, json={"message": "down"}) for _ in range(4)],
    )
    events: list[tuple[EventKind, dict[str, object]]] = []
    client = make_client(recorded, events)
    issue = await client.get_issue(1, task_id=TASK)
    assert issue["title"] == "ok"
    assert client.sleeps == [5, 20]  # type: ignore[attr-defined]
    with pytest.raises(GitHubApiError, match="422") as exc:
        await client.get_issue(2, task_id=TASK)
    assert exc.value.status == 422 and exc.value.path.endswith("/issues/2")
    with pytest.raises(GitHubApiError, match="503"):
        await client.get_issue(3, task_id=TASK)
    assert client.sleeps == [5, 20, 5, 20, 60]  # type: ignore[attr-defined]


# --- CODEOWNERS -----------------------------------------------------------------


def test_codeowners_resolution() -> None:
    rules = parse_codeowners(
        "# owners\n"
        "*            @acme/platform\n"
        "*.js         @frontend\n"
        "/docs/       @writers\n"
        "/.factory/   @acme/tech-leads\n"
        "src/billing/ @billing-owner @acme/tech-leads\n"
        "**/migrations/*.sql @dba\n"
    )
    assert owners_for("README.md", rules) == ("@acme/platform",)
    assert owners_for("web/app.js", rules) == ("@frontend",)
    assert owners_for("docs/guide.md", rules) == ("@writers",)
    assert owners_for(".factory/roster.yaml", rules) == ("@acme/tech-leads",)
    assert owners_for("src/billing/invoice.py", rules) == ("@billing-owner", "@acme/tech-leads")
    assert owners_for("db/migrations/0042_due_date.sql", rules) == ("@dba",)
    assert owners_for("src/billing/migrations/0001.sql", rules) == ("@dba",)  # last rule wins
    assert owners_for("anything", []) == ()
