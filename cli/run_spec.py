"""``factory run-spec``: the phase 1 entry point on a hand-written spec."""

import asyncio
from pathlib import Path

from cli.common import open_trace, say, settings_for, warn
from orchestrator.config import load_factory_config
from orchestrator.errors import FactoryError
from orchestrator.gates.selftest import assert_self_test, self_test
from orchestrator.harness.fake import FakeScript
from orchestrator.harness.registry import make_harness
from orchestrator.issue_to_pr import run_build_slice
from orchestrator.models.enums import AgentRole, WorkspaceKind
from orchestrator.phases.build import BuildDeps, BuildOutcome
from orchestrator.version import factory_version
from orchestrator.workspace.worktree import create_worktree


def _task_id(org: str, repo_name: str, issue: int) -> str:
    return f"{org}/{repo_name}#{issue}"


async def _run(
    *,
    repo: Path,
    issue: int,
    spec: str | None,
    harness_name: str | None,
    scripts: Path | None,
    data_dir: Path | None,
    org: str,
    budget_usd: float,
    skip_self_test: bool,
    repo_name: str | None = None,
) -> BuildOutcome:
    repo = repo.resolve()
    settings = settings_for(data_dir or repo / ".factory-dev" / "data")
    config = load_factory_config(repo / ".factory")
    if harness_name is not None:
        builder = config.roster.builder.model_copy(
            update={
                "harness": harness_name,
                "vendor": config.roster.builder.vendor_name,
            }
        )
        config = config.model_copy(
            update={"roster": config.roster.model_copy(update={"builder": builder})}
        )
    role = config.roster.builder

    if not skip_self_test:
        fixture = repo / ".factory-dev" / "known_failing"
        report = await self_test(config.quality, fixture, env=settings.subprocess_base_env())
        assert_self_test(report)
        say(f"self-test: {len(report.checks)} commands fail the known-failing fixture")

    fake_scripts: list[FakeScript] | None = None
    if role.harness == "fake":
        script_path = scripts or repo / ".factory-dev" / "scripts" / f"{issue}.json"
        fake_scripts = [FakeScript.from_file(script_path)]
    harness = make_harness(role, settings, fake_scripts=fake_scripts)

    db, writer = open_trace(settings)
    try:
        writer.load_config_version(**config.config_version_row())
        if writer.get_installation() is None:
            writer.insert_installation(
                org=org,
                repo=repo.name,
                factory_version=factory_version(),
                config_sha=config.config_sha,
                per_task_budget_usd=budget_usd,
                export_pubkey="dev",
                concurrency_n=config.settings.concurrency_n,
            )
        spec_rel = spec or _find_spec(repo, issue)
        task_id = _task_id(org, repo_name or repo.name, issue)
        writer.insert_task(
            task_id=task_id,
            issue_number=issue,
            title=spec_rel,
            branch=f"agent/{issue}",
            workspace_kind=WorkspaceKind.WORKTREE,
            budget_usd=budget_usd,
            admitted_by="operator",
            config_sha=config.config_sha,
        )
        task = writer.get_task(task_id)
        attempt = int(task["attempt"]) if task is not None and task["status"] != "queued" else 1
        if task is not None and task["status"] != "queued":
            attempt += 1
        worktree = create_worktree(repo, issue=issue, worktrees_dir=settings.worktrees_dir)
        deps = BuildDeps(writer=writer, config=config, settings=settings, harness=harness)
        say(f"task {task_id}: attempt {attempt} on {worktree.branch} with harness {harness.name}")
        return await run_build_slice(
            deps, task_id=task_id, worktree=worktree, spec_rel=spec_rel, attempt=attempt
        )
    finally:
        db.close()


def _find_spec(repo: Path, issue: int) -> str:
    matches = sorted((repo / "specs").glob(f"{issue}-*.md"))
    if not matches:
        raise FactoryError(f"no spec named specs/{issue}-*.md in {repo}")
    return f"specs/{matches[0].name}"


def run_spec(
    *,
    repo: Path,
    issue: int,
    spec: str | None = None,
    harness: str | None = None,
    scripts: Path | None = None,
    data_dir: Path | None = None,
    org: str = "dev",
    budget_usd: float = 6.0,
    skip_self_test: bool = False,
) -> int:
    try:
        outcome = asyncio.run(
            _run(
                repo=repo,
                issue=issue,
                spec=spec,
                harness_name=harness,
                scripts=scripts,
                data_dir=data_dir,
                org=org,
                budget_usd=budget_usd,
                skip_self_test=skip_self_test,
            )
        )
    except FactoryError as exc:
        warn(f"error ({exc.code}): {exc}")
        return 1
    if outcome.passed:
        say(
            f"pass · commit {(outcome.commit_sha or '')[:10]} · cost ${outcome.cost_usd:.2f}"
            f" · builder calls {outcome.builder_calls} · repairs {outcome.repairs}"
        )
        return 0
    say(
        f"blocked ({outcome.blocked_reason}) · cost ${outcome.cost_usd:.2f}"
        f" · builder calls {outcome.builder_calls} · repairs {outcome.repairs}"
    )
    tail = outcome.evidence.strip().splitlines()[-20:]
    for line in tail:
        say("  " + line)
    return 1


__all__ = ["AgentRole", "run_spec"]
