"""``factory dev issues``: open the seed's issues on the scratch repository under the App."""

import asyncio
from pathlib import Path

import httpx

from cli.common import say, settings_for, warn
from cli.seed import ALL_TASKS, issue_body
from orchestrator.github.auth import InstallationTokenMinter
from orchestrator.github.client import GitHubClient
from orchestrator.service import GITHUB_API


async def _create(*, org: str, repo: str, data_dir: Path | None) -> int:
    settings = settings_for(data_dir)
    if (
        settings.github_app_id is None
        or settings.github_installation_id is None
        or settings.github_private_key_file is None
    ):
        warn(
            "set FACTORY_GITHUB_APP_ID, FACTORY_GITHUB_INSTALLATION_ID and"
            " FACTORY_GITHUB_PRIVATE_KEY_FILE"
        )
        return 2
    pem = settings.github_private_key_file.read_text(encoding="utf-8")
    async with httpx.AsyncClient(base_url=GITHUB_API, timeout=30.0) as http:
        minter = InstallationTokenMinter(
            app_id=settings.github_app_id,
            private_key_pem=pem,
            installation_id=settings.github_installation_id,
            http=http,
        )
        client = GitHubClient(owner=org, repo=repo, tokens=minter, http=http)
        mismatches = 0
        for task in ALL_TASKS:
            created = await client.create_issue(
                title=task.name,  # the planner names the spec after the title
                body=issue_body(task),
                task_id="dev-issues",
            )
            number = int(created["number"])
            flag = (
                "" if number == task.issue else f"  (seed script is {task.issue}.json: rename it)"
            )
            if number != task.issue:
                mismatches += 1
            say(f"#{number} {task.name}{flag}")
        if mismatches:
            warn(
                f"{mismatches} issue number(s) differ from the seed; rename the scripts under"
                " .factory-dev/scripts before serving with --harness fake"
            )
    return 0


def create_issues(*, org: str, repo: str, data_dir: Path | None) -> int:
    return asyncio.run(_create(org=org, repo=repo, data_dir=data_dir))
