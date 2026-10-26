"""``factory protection``: the branch-protection check the installer runs (PRD §Capability 5).

The base branch must require CODEOWNER review; otherwise a ``risk:governed``
PR could merge without the owner the factory requested. The result is one
line in the install log; a missing rule exits 1 so ``factory install``
(unit 44) refuses to go live on it.
"""

import asyncio
from pathlib import Path

import httpx

from cli.common import say, settings_for, warn
from orchestrator.budget.dry_run import InstallLog
from orchestrator.github.auth import InstallationTokenMinter
from orchestrator.github.client import GitHubClient
from orchestrator.github.protection import ProtectionReport, check_branch_protection
from orchestrator.models.settings import Settings

GITHUB_API = "https://api.github.com"


async def protection_report(
    settings: Settings,
    *,
    org: str,
    repo: str,
    branch: str | None,
    http: httpx.AsyncClient | None = None,
) -> ProtectionReport:
    if (
        settings.github_app_id is None
        or settings.github_installation_id is None
        or settings.github_private_key_file is None
    ):
        raise ValueError(
            "set FACTORY_GITHUB_APP_ID, FACTORY_GITHUB_INSTALLATION_ID and"
            " FACTORY_GITHUB_PRIVATE_KEY_FILE"
        )
    pem = settings.github_private_key_file.read_text(encoding="utf-8")
    own = http is None
    client_http = http or httpx.AsyncClient(base_url=GITHUB_API, timeout=30.0)
    try:
        minter = InstallationTokenMinter(
            app_id=settings.github_app_id,
            private_key_pem=pem,
            installation_id=settings.github_installation_id,
            http=client_http,
        )
        client = GitHubClient(owner=org, repo=repo, tokens=minter, http=client_http)
        target = branch or str((await client.get_repository(task_id="install"))["default_branch"])
        return await check_branch_protection(client, target)
    finally:
        if own:
            await client_http.aclose()


def check_protection(
    *,
    org: str,
    repo: str,
    branch: str | None,
    data_dir: Path | None,
    http: httpx.AsyncClient | None = None,
) -> int:
    settings = settings_for(data_dir)
    try:
        report = asyncio.run(
            protection_report(settings, org=org, repo=repo, branch=branch, http=http)
        )
    except ValueError as exc:
        warn(str(exc))
        return 2
    log = InstallLog(settings.data_dir)
    log.append(report.record())
    if report.required:
        say(f"{org}/{repo}@{report.branch}: CODEOWNER review required ({report.detail})")
        return 0
    warn(
        f"{org}/{repo}@{report.branch}: CODEOWNER review is not required; {report.detail}."
        " Add a repository ruleset (pull request rule with 'require code owner review')"
        " before going live."
    )
    return 1
