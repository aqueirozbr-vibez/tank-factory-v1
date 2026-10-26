"""Unit 43: governed PRs request the CODEOWNER; the base branch must require that review."""

from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from cli.protection import check_protection
from orchestrator.budget.dry_run import InstallLog
from orchestrator.github.protection import check_branch_protection, codeowner_review_required
from orchestrator.issue_to_pr import deliver
from orchestrator.phases.approval import PlanApprovals
from orchestrator.trace import TraceWriter
from orchestrator.workspace.git import run_git
from tests.unit.test_build_phase import SPEC_REL, TASK_ID
from tests.unit.test_deliver import FakePulls, accepted_pipeline, add_remote
from tests.unit.test_github_client import Recorded, fixture, make_client

RULES_PATH = "/repos/acme/atlas-api/rules/branches/main"
PROTECTION_PATH = "/repos/acme/atlas-api/branches/main/protection"
OWNER_PARAMS: dict[str, object] = {
    "required_approving_review_count": 1,
    "dismiss_stale_reviews_on_push": False,
    "require_code_owner_review": True,
    "require_last_push_approval": False,
    "required_review_thread_resolution": False,
}
OWNER_RULE: dict[str, object] = {
    "type": "pull_request",
    "ruleset_source_type": "Repository",
    "ruleset_source": "atlas-api",
    "ruleset_id": 42,
    "parameters": OWNER_PARAMS,
}
WEAK_RULE: dict[str, object] = {
    **OWNER_RULE,
    "parameters": {**OWNER_PARAMS, "require_code_owner_review": False},
}
DELETION_RULE = {"type": "deletion", "ruleset_id": 7, "ruleset_source": "atlas-api"}
CLASSIC = {"required_pull_request_reviews": {"require_code_owner_reviews": True}}


def test_codeowner_review_required_table() -> None:
    by_ruleset = codeowner_review_required("main", [DELETION_RULE, OWNER_RULE], None)
    assert by_ruleset.required and by_ruleset.source == "ruleset" and "42" in by_ruleset.detail
    by_classic = codeowner_review_required("main", [WEAK_RULE], CLASSIC)
    assert by_classic.required and by_classic.source == "branch_protection"
    nothing = codeowner_review_required("main", [DELETION_RULE], None)
    assert not nothing.required and nothing.source is None
    assert "rules seen: deletion" in nothing.detail and "absent or unreadable" in nothing.detail
    weak = codeowner_review_required("main", [WEAK_RULE], {"required_pull_request_reviews": {}})
    assert not weak.required and "without code owner reviews" in weak.detail
    odd = codeowner_review_required("main", [{"type": "pull_request", "parameters": "x"}], None)
    assert not odd.required
    assert by_ruleset.record()["kind"] == "branch_protection"


async def test_client_reads_rules_and_treats_forbidden_protection_as_unreadable() -> None:
    recorded = Recorded()
    recorded.on("GET", RULES_PATH, 200, [DELETION_RULE, OWNER_RULE])
    recorded.on("GET", PROTECTION_PATH, 403, {"message": "Resource not accessible by integration"})
    client = make_client(recorded, [])
    report = await check_branch_protection(client, "main")
    assert report.required and report.source == "ruleset"
    assert await client.get_branch_protection("main", task_id="t") is None

    recorded.on("GET", RULES_PATH, 200, [])
    recorded.on("GET", PROTECTION_PATH, 200, CLASSIC)
    classic = await check_branch_protection(client, "main")
    assert classic.required and classic.source == "branch_protection"

    recorded.on("GET", PROTECTION_PATH, 404, {"message": "Branch not protected"})
    missing = await check_branch_protection(client, "main")
    assert not missing.required
    assert {r.url.path for r in recorded.requests} == {RULES_PATH, PROTECTION_PATH}


def test_cli_check_records_the_verdict_in_the_install_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    key_file = tmp_path / "app.pem"
    key_file.write_text(private_pem, encoding="utf-8")
    monkeypatch.setenv("FACTORY_GITHUB_APP_ID", "12345")
    monkeypatch.setenv("FACTORY_GITHUB_INSTALLATION_ID", "77")
    monkeypatch.setenv("FACTORY_GITHUB_PRIVATE_KEY_FILE", str(key_file))
    data_dir = tmp_path / "data"
    recorded = Recorded()
    recorded.on("POST", "/app/installations/77/access_tokens", 201, fixture("access_token.json"))
    recorded.on("GET", "/repos/acme/atlas-api", 200, {"default_branch": "main"})
    recorded.on("GET", RULES_PATH, 200, [OWNER_RULE])
    recorded.on("GET", PROTECTION_PATH, 403, {"message": "forbidden"})
    code = check_protection(
        org="acme", repo="atlas-api", branch=None, data_dir=data_dir, http=recorded.client()
    )
    assert code == 0
    recorded.on("GET", RULES_PATH, 200, [DELETION_RULE])
    refused = check_protection(
        org="acme", repo="atlas-api", branch="main", data_dir=data_dir, http=recorded.client()
    )
    assert refused == 1
    records = InstallLog(data_dir).records()
    assert [(r["kind"], r["required"]) for r in records] == [
        ("branch_protection", True),
        ("branch_protection", False),
    ]
    assert records[0]["source"] == "ruleset" and records[0]["branch"] == "main"

    monkeypatch.delenv("FACTORY_GITHUB_APP_ID")
    assert check_protection(org="acme", repo="atlas-api", branch=None, data_dir=data_dir) == 2


async def test_governed_pr_requests_the_codeowner_from_the_clone(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    (product / "CODEOWNERS").write_text(
        "# owners\nsrc/billing/ @mlima @acme/platform\n", encoding="utf-8", newline="\n"
    )
    run_git(["add", "CODEOWNERS"], cwd=product)
    run_git(["commit", "-q", "-m", "owners"], cwd=product)
    add_remote(product, tmp_path)
    outcome, wt, (deps, _) = await accepted_pipeline(trace_writer, product, tmp_path)
    github = FakePulls()
    approvals = PlanApprovals(writer=trace_writer, github=github, repo_clone=product)  # type: ignore[arg-type]
    owners = approvals.owners_of("src/billing/invoice.py")
    assert owners == ("@mlima", "@acme/platform")
    assert approvals.owners_of("README.md") == ()
    delivered = await deliver(
        deps,
        task_id=TASK_ID,
        worktree=wt,
        spec_rel=SPEC_REL,
        outcome=outcome,
        github=github,
        base_branch="main",
        owners=owners,
    )
    assert delivered.delivered
    assert ("request_reviewers", (431, ("mlima",), ("platform",))) in github.calls
