"""The composition root: intake, scheduler, runner and API in one process (TRD §2.0).

``FactoryService`` wires the pieces the earlier units built; ``build_service``
does it from settings for ``factory serve``. Tests build the service with a
fake GitHub and the keyed fake harness.
"""

import asyncio
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi import FastAPI

from orchestrator import constants
from orchestrator.api.app import ApiDeps, create_app
from orchestrator.api.controls import ControlDeps
from orchestrator.api.limits import RateLimiter
from orchestrator.authz import resolve_permission, verify_generated
from orchestrator.budget.keys import PhaseKeys
from orchestrator.budget.median import MedianBudget
from orchestrator.budget.proxy import ProxyAdmin
from orchestrator.config import load_factory_config
from orchestrator.eval import EvalItem, EvalRunner
from orchestrator.github.auth import InstallationTokenMinter
from orchestrator.github.client import GitHubApiError, GitHubClient
from orchestrator.github.codeowners import is_owner, load_codeowners, owners_for
from orchestrator.harness.base import HarnessAdapter
from orchestrator.harness.claude_code import Runner
from orchestrator.harness.fake import KeyedFakeHarness
from orchestrator.harness.process import run_with_timeout
from orchestrator.harness.registry import make_harness, probe_harness_versions
from orchestrator.intake import Admission, Cancellation, Handlers, Poller, Router, Scheduler
from orchestrator.intake.ci import CiReentry
from orchestrator.intake.issue_fetcher import TaggedIssue, fetch_issue
from orchestrator.intake.resume import Resume
from orchestrator.intake.webhook import GitHubIpRanges
from orchestrator.models.config import FactoryConfig
from orchestrator.models.enums import AgentRole, EventKind
from orchestrator.models.events import NormalisedEvent
from orchestrator.models.settings import Settings
from orchestrator.notify.slack import SlackNotifier, stuck_run_notice
from orchestrator.phases.approval import PlanApprovals
from orchestrator.phases.deliver import MergeObserver
from orchestrator.phases.plan import HoldoutPort
from orchestrator.runner import HarnessFactory, RunnerDeps, RunnerGitHub, TaskRunner, TokenFor
from orchestrator.supervisor import Supervisor
from orchestrator.trace import TraceDB, TraceWriter
from orchestrator.trace.kpi import KpiMaterialiser
from orchestrator.trace.retention import RetentionJob
from orchestrator.version import factory_version
from orchestrator.watchdogs import KillSwitch, StuckProcess, Watchdog, sweep_on_start
from orchestrator.workspace.container import DockerCli, ensure_network
from orchestrator.workspace.holdout import HoldoutCheckout
from orchestrator.workspace.hooks import install_codex_rules, verify_hook
from orchestrator.workspace.image import ensure_image
from orchestrator.workspace.worktree import default_branch

GITHUB_API = "https://api.github.com"


class FactoryService:
    def __init__(
        self,
        *,
        settings: Settings,
        writer: TraceWriter,
        config: FactoryConfig,
        github: RunnerGitHub,
        repo_clone: Path,
        harnesses: Mapping[AgentRole, HarnessAdapter],
        base_branch: str,
        holdout: HoldoutPort,
        token_for: TokenFor | None = None,
        ip_ranges: GitHubIpRanges | None = None,
        poller: Poller | None = None,
        keys: PhaseKeys | None = None,
        docker: DockerCli | None = None,
        task_image: str | None = None,
        harness_for: HarnessFactory | None = None,
    ) -> None:
        self.settings = settings
        self.writer = writer
        self.config = config
        self.github = github
        self.repo_clone = repo_clone
        self.harnesses = harnesses
        self.holdout = holdout
        self.base_branch = base_branch
        self.inbox: asyncio.Queue[NormalisedEvent] = asyncio.Queue()
        self.notifier = (
            SlackNotifier(settings.slack_webhook_url, writer)
            if settings.slack_webhook_url
            else None
        )
        self.approvals = PlanApprovals(
            writer=writer, github=github, repo_clone=repo_clone, notifier=self.notifier
        )
        self.runner = TaskRunner(
            RunnerDeps(
                writer=writer,
                config=config,
                settings=settings,
                harnesses=harnesses,
                github=github,
                repo_clone=repo_clone,
                holdout=holdout,
                approvals=self.approvals,
                base_branch=base_branch,
                token_for=token_for,
                keys=keys or PhaseKeys(),
                docker=docker,
                task_image=task_image,
                harness_for=harness_for,
            )
        )
        self.supervisor = Supervisor(self._run_task, on_finished=self._on_finished)
        self.scheduler = Scheduler(writer, self.supervisor)
        self.admission = Admission(
            writer=writer,
            github=github,
            settings=settings,
            repo_clone=repo_clone,
            scheduler=self.scheduler,
        )
        self.cancellation = Cancellation(
            writer=writer, github=github, supervisor=self.supervisor, scheduler=self.scheduler
        )
        self.resume = Resume(writer=writer, github=github, scheduler=self.scheduler)
        self.kill_switch = KillSwitch(
            writer, supervisor=self.supervisor, docker=docker, github=github
        )
        self.kpis = KpiMaterialiser(writer)
        self.median_budget = MedianBudget(writer)
        self.watchdog = Watchdog(writer, on_stuck=self._on_stuck)
        self.retention = RetentionJob(writer)
        self.merges = MergeObserver(writer, on_merge=self.kpis.on_merge)
        self.ci = CiReentry(writer=writer, scheduler=self.scheduler)
        self.router = Router(
            writer,
            Handlers(
                admit=self.admission.admit,
                cancel=self.cancellation.cancel,
                plan_approved=self.approvals.on_label,
                resume=self.resume.on_unlabeled,
                merged=self.merges.on_merged,
                review=self.ci.on_changes_requested,
                ci_failed=self.ci.on_ci_failed,
            ),
        )
        ranges = ip_ranges or GitHubIpRanges(
            (), datetime.now(UTC), trust_loopback=settings.trust_loopback
        )
        limiter = RateLimiter()
        self.controls = ControlDeps(
            writer=writer,
            settings=settings,
            limiter=limiter,
            permission_for=self._permission_for,
            codeowner_of=self._codeowner_of,
            scheduler=self.scheduler,
            kill_switch=self.kill_switch,
            resume=self.resume,
            start_eval=self._start_eval,
        )
        self.api: FastAPI = create_app(
            ApiDeps(
                writer=writer,
                settings=settings,
                ip_ranges=ranges,
                inbox=self.inbox,
                kill_switch=self.kill_switch,
                repo_clone=repo_clone,
                limiter=limiter,
                controls=self.controls,
            )
        )
        self.poller = poller
        self._eval_tasks: set[asyncio.Task[object]] = set()

    async def _run_task(self, task_id: str) -> None:
        await self.runner.run(task_id)

    def _on_finished(self, task_id: str) -> None:
        self.scheduler.on_finished(task_id)

    def start(self) -> list[str]:
        """The restart sweep: orphaned work closed, running tasks requeued (TRD §7.8)."""
        requeued = sweep_on_start(self.writer)
        self.writer.write_event(
            EventKind.LOG,
            "orchestrator",
            payload={"service": "start", "version": factory_version(), "requeued": requeued},
        )
        return requeued

    async def _permission_for(self, login: str) -> str | None:
        """The console actor's GitHub permission, cached five minutes; None when unreachable."""
        return await resolve_permission(self.writer, self.github, login, task_id="console")

    async def _codeowner_of(self, login: str, path: str) -> bool:
        return is_owner(login, owners_for(path, load_codeowners(self.repo_clone)))

    async def _start_eval(self, set_id: str, actor: str) -> str:
        """Start a manual eval replay in this process and answer with its run id."""
        runner = EvalRunner(
            writer=self.writer,
            settings=self.settings,
            config=self.config,
            repo_clone=self.repo_clone,
            harnesses=self.harnesses,
            holdout=self.holdout,
            issues=self._eval_issue,
            base_branch=self.base_branch,
        )
        loop = asyncio.get_running_loop()
        started: asyncio.Future[str] = loop.create_future()

        def on_started(run_id: str) -> None:
            if not started.done():
                started.set_result(run_id)

        async def run() -> object:
            try:
                return await runner.run(set_id, trigger="manual", on_started=on_started)
            except Exception as exc:  # the run's own row records the interruption
                if not started.done():
                    started.set_exception(exc)
                raise

        task = asyncio.ensure_future(run())
        self._eval_tasks.add(task)
        task.add_done_callback(self._eval_tasks.discard)
        return await started

    async def _eval_issue(self, item: EvalItem) -> TaggedIssue:
        return await fetch_issue(
            self.github, item.issue_number, task_id=f"eval#{item.issue_number}"
        )

    async def _on_stuck(self, process: StuckProcess) -> None:
        """The stuck flag is already in the trace; Slack hears of it when configured."""
        if self.notifier is None:
            return
        task = self.writer.get_task(process.task_id)
        await self.notifier.send(
            stuck_run_notice(
                task_id=process.task_id,
                issue_number=None if task is None else int(task["issue_number"]),
                title=None if task is None else task["title"],
                phase_name=process.name,
                idle_s=int(process.idle_s),
                threshold_s=constants.STUCK_AFTER_S,
                last_event_at=process.last_event_at,
            )
        )

    async def run(self, stop: asyncio.Event) -> None:
        """Dispatch, then drive the router, the switch (and the poller) until ``stop``."""
        if not self.settings.enabled and self.kill_switch.enabled():
            await self.kill_switch.disable(actor="env", reason="FACTORY_ENABLED=0 at start")
        await self.scheduler.dispatch()
        loops = [
            self.router.run(self.inbox, stop),
            self.kill_switch.run(stop),
            self.kpis.run(stop),
            self.median_budget.run(stop),
            self.watchdog.run(stop),
            self.retention.run(stop),
        ]
        if self.poller is not None:
            loops.append(self.poller.run(stop))
        await asyncio.gather(*loops)

    async def drain(self) -> None:
        """Process every queued event and wait for every live task (tests and shutdown)."""
        while True:
            await self.scheduler.settle()
            while not self.inbox.empty():
                await self.router.handle(self.inbox.get_nowait())
            await self.scheduler.dispatch()
            live = self.supervisor.live
            if not live:
                await self.scheduler.settle()
                if self.inbox.empty() and not self.supervisor.live:
                    return
                continue
            for task_id in live:
                await self.supervisor.wait(task_id)
            await asyncio.sleep(0)  # let on_finished schedule its dispatch


@dataclass(frozen=True)
class BuiltService:
    service: FactoryService
    db: TraceDB
    http: httpx.AsyncClient


async def build_service(
    settings: Settings,
    *,
    repo_clone: Path,
    org: str,
    repo: str,
    per_task_budget_usd: float,
    harness_mode: str = "roster",
    scripts_dir: Path | None = None,
    with_poller: bool = True,
    poll_interval_s: float | None = None,
) -> BuiltService:
    """Production wiring from settings; the App credentials come from the environment."""
    if settings.github_app_id is None or settings.github_installation_id is None:
        raise ValueError("FACTORY_GITHUB_APP_ID and FACTORY_GITHUB_INSTALLATION_ID are required")
    if settings.github_private_key_file is None:
        raise ValueError("FACTORY_GITHUB_PRIVATE_KEY_FILE is required")
    pem = settings.github_private_key_file.read_text(encoding="utf-8")
    repo_clone = repo_clone.resolve()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db = TraceDB.open(settings.trace_db_path)
    writer = TraceWriter(db)
    verify_generated()  # a stale or broken authz table stops start-up (TRD §6.2)
    config = load_factory_config(repo_clone / ".factory")
    writer.load_config_version(**config.config_version_row())
    if writer.get_installation() is None:
        writer.insert_installation(
            org=org,
            repo=repo,
            factory_version=factory_version(),
            config_sha=config.config_sha,
            per_task_budget_usd=per_task_budget_usd,
            export_pubkey="pending",
            timezone=settings.timezone,
        )
    http = httpx.AsyncClient(base_url=GITHUB_API, timeout=30.0)
    minter = InstallationTokenMinter(
        app_id=settings.github_app_id,
        private_key_pem=pem,
        installation_id=settings.github_installation_id,
        http=http,
    )

    def record(kind: EventKind, payload: dict[str, object]) -> None:
        writer.write_event(kind, "orchestrator", payload=payload)

    client = GitHubClient(owner=org, repo=repo, tokens=minter, http=http, recorder=record)
    try:
        meta = await client.get_meta(task_id="startup")
        ranges = GitHubIpRanges.from_meta(meta)
        if settings.trust_loopback:
            ranges = GitHubIpRanges(ranges.cidrs, ranges.fetched_at, trust_loopback=True)
    except (GitHubApiError, httpx.HTTPError):
        ranges = GitHubIpRanges((), datetime.now(UTC), trust_loopback=settings.trust_loopback)
    docker: DockerCli | None = None
    task_image: str | None = None
    harness_for: HarnessFactory | None = None
    if settings.workspace == "container":
        docker = DockerCli(settings.docker_bin, env_dir=settings.data_dir / "env")
        await ensure_network(docker, settings.docker_network)
        task_image = settings.task_image
        if task_image is None:
            built = await ensure_image(
                repo_clone,
                org=org,
                repo_name=repo,
                docker=docker,
                install_command=config.settings.install_command,
            )
            task_image = built.tag
    await verify_hook(  # fail closed: no agent phase without a hook that denies
        run_with_timeout, hooks_dir=str(settings.hooks_dir), python=sys.executable, cwd=Path.cwd()
    )
    install_codex_rules(settings.data_dir / "codex-home")
    harnesses: dict[AgentRole, HarnessAdapter]
    if harness_mode == "fake":
        if scripts_dir is None:
            raise ValueError("--scripts-dir is required with --harness fake")
        keyed = KeyedFakeHarness.from_seed_dir(scripts_dir)
        harnesses = dict.fromkeys(AgentRole, keyed)

        def fake_for(
            role: AgentRole, runner: Runner, versions: Mapping[str, str]
        ) -> HarnessAdapter:
            return keyed.with_runner(runner)

        harness_for = fake_for
    else:
        role_configs = {
            AgentRole.PLANNER: config.roster.planner,
            AgentRole.BUILDER: config.roster.builder,
            AgentRole.REVIEWER: config.roster.reviewer,
        }
        harnesses = {role: make_harness(cfg, settings) for role, cfg in role_configs.items()}

        def harness_for(
            role: AgentRole, runner: Runner, versions: Mapping[str, str]
        ) -> HarnessAdapter:
            cfg = role_configs[role]
            return make_harness(cfg, settings, runner=runner, version=versions.get(cfg.harness))

        versions = (
            probe_harness_versions(harnesses)  # fail closed on an unreadable binary
            if settings.workspace != "container"
            else {}  # read per task container, where the binaries live (unit 45)
        )
        writer.write_event(
            EventKind.LOG,
            "orchestrator",
            payload={
                "roster": {
                    str(role): {
                        "harness": cfg.harness,
                        "model": cfg.model,
                        "vendor": cfg.vendor_name,
                        "version": versions.get(str(role)),
                    }
                    for role, cfg in role_configs.items()
                }
            },
        )

    holdout = HoldoutCheckout(
        str(repo_clone), settings.holdout_dir, branch=config.settings.holdout_branch
    )
    holdout.ensure()
    base = default_branch(repo_clone)
    poller = None
    if with_poller:
        interval = constants.POLL_INTERVAL_S if poll_interval_s is None else poll_interval_s
        poller = Poller(
            client,
            writer,
            asyncio.Queue(),  # replaced below with the service inbox
            interval_s=interval,
            main_branch=base,
        )

    async def token_for(task_id: str) -> str | None:
        return await minter.token_for(task_id)

    keys = PhaseKeys()
    if settings.proxy_url:
        if not settings.proxy_master_key:
            raise ValueError("FACTORY_PROXY_MASTER_KEY is required with FACTORY_PROXY_URL")
        admin = ProxyAdmin(settings.proxy_url, settings.proxy_master_key)
        if not await admin.health():  # fail closed: no agent starts without the proxy
            raise ValueError(f"model proxy at {settings.proxy_url} is unreachable")
        keys = PhaseKeys(
            admin,
            task_proxy_url=settings.proxy_url_in_tasks
            if settings.workspace == "container"
            else None,
        )

    service = FactoryService(
        settings=settings,
        writer=writer,
        config=config,
        github=client,
        repo_clone=repo_clone,
        harnesses=harnesses,
        base_branch=base,
        holdout=holdout,
        token_for=token_for,
        ip_ranges=ranges,
        poller=poller,
        keys=keys,
        docker=docker,
        task_image=task_image,
        harness_for=harness_for,
    )
    if poller is not None:
        poller.inbox = service.inbox
    return BuiltService(service=service, db=db, http=http)
