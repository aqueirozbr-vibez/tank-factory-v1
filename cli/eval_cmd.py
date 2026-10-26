"""``factory eval run``: replay an eval set and score it (PRD §Capability 5 · Eval set).

``--fixture`` replays the 24-issue fixture set with the fake harness on a
seeded scratch repository (the regression gate's offline form); ``--set``
replays a set recorded in the trace against the repository's real issues
with the roster's harnesses.
"""

import asyncio
import dataclasses
import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import httpx

from cli.common import open_trace, say, settings_for, warn
from cli.seed import (
    TASKS,
    SeedTask,
    issue_body,
    planner_script_for,
    reviewer_script_for,
    script_for,
)
from orchestrator import constants
from orchestrator.config import load_factory_config
from orchestrator.errors import FactoryError
from orchestrator.eval import EvalItem, EvalReport, EvalRunner, ensure_eval_set
from orchestrator.eval.runner import Trigger
from orchestrator.github.auth import InstallationTokenMinter
from orchestrator.github.client import GitHubClient
from orchestrator.harness.base import HarnessAdapter
from orchestrator.harness.fake import FakeScript, KeyedFakeHarness
from orchestrator.harness.registry import make_harness
from orchestrator.intake.issue_fetcher import TaggedIssue, fetch_issue, parse_sections
from orchestrator.models.enums import AgentRole
from orchestrator.version import factory_version
from orchestrator.workspace.holdout import HoldoutCheckout

FACTORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_SET = FACTORY_ROOT / "fixtures" / "eval" / "set.json"
GITHUB_API = "https://api.github.com"


@dataclasses.dataclass(frozen=True)
class FixtureItem:
    issue: int
    eval_class: str
    seed: int

    @property
    def task(self) -> SeedTask:
        return dataclasses.replace(TASKS[self.seed - 1], issue=self.issue)


def load_fixture(path: Path = FIXTURE_SET) -> tuple[str, list[FixtureItem]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    items = [FixtureItem(int(i["issue"]), str(i["class"]), int(i["seed"])) for i in data["items"]]
    return str(data["name"]), items


def fixture_harness(
    items: list[FixtureItem], *, failing: Mapping[int, str] | None = None
) -> KeyedFakeHarness:
    """The seed scripts keyed by the fixture's issue numbers; ``failing`` injects regressions."""
    scripts: dict[tuple[str, int], list[FakeScript]] = {}
    for item in items:
        task = item.task
        scripts[("planner", item.issue)] = [FakeScript.model_validate(planner_script_for(task))]
        builder = FakeScript.model_validate(script_for(task, violation=False))
        if failing and item.issue in failing:
            builder = FakeScript(fail_with=failing[item.issue])
        scripts[("builder", item.issue)] = [builder]
        scripts[("reviewer", item.issue)] = [FakeScript.model_validate(reviewer_script_for(task))]
    return KeyedFakeHarness(scripts)


def fixture_issue_source(items: list[FixtureItem]) -> Any:  # noqa: ANN401 - an IssueSource
    by_issue = {item.issue: item.task for item in items}

    async def source(item: EvalItem) -> TaggedIssue:
        task = by_issue[item.issue_number]
        body = issue_body(task)
        return TaggedIssue(
            number=item.issue_number,
            title=task.name,
            author_login="eval",
            author_association="MEMBER",
            trusted=True,
            body=body,
            sections=parse_sections(body),
            comments=(),
        )

    return source


def print_report(report: EvalReport) -> None:
    classes = ", ".join(f"{k} {v:.1f}" for k, v in sorted(report.per_class.items()))
    baseline = (
        f"; baseline {report.baseline_pass_rate:.1f} (run {report.baseline_run_id}),"
        f" delta {report.delta_points:+.1f} points"
        if report.baseline_pass_rate is not None and report.delta_points is not None
        else "; no baseline"
    )
    verdict = "BLOCKED" if report.blocked else "ok"
    say(
        f"eval run {report.run_id}: {report.passed_count}/{len(report.results)} passed,"
        f" {report.pass_rate:.1f} points overall ({classes}){baseline} → {verdict}"
    )
    if report.reference_pass_rate is not None and report.gap_points is not None:
        within = report.gap_points >= -constants.EVAL_GAP_POINTS
        verdict = "within" if within else "wider than"
        say(
            f"  gap {report.gap_points:+.1f} points against the reference"
            f" {report.reference_pass_rate:.1f} ({verdict} {constants.EVAL_GAP_POINTS})"
        )
    for result in report.results:
        if not result.passed:
            say(f"  #{result.item.issue_number} [{result.item.eval_class}] {result.failure_reason}")


async def _run(
    *,
    repo_clone: Path,
    org: str,
    repo: str,
    fixture: bool,
    set_name: str | None,
    trigger: str,
    trigger_ref: str | None,
    baseline: str | None,
    data_dir: Path | None,
    harness_mode: str,
    reference_pass_rate: float | None = None,
) -> int:
    repo_clone = repo_clone.resolve()
    settings = settings_for(data_dir or repo_clone / ".factory-dev" / "data")
    config = load_factory_config(repo_clone / ".factory")
    db, writer = open_trace(settings)
    try:
        writer.load_config_version(**config.config_version_row())
        if writer.get_installation() is None:
            writer.insert_installation(
                org=org,
                repo=repo,
                factory_version=factory_version(),
                config_sha=config.config_sha,
                per_task_budget_usd=6.0,
                export_pubkey="dev",
                concurrency_n=config.settings.concurrency_n,
            )
        holdout = HoldoutCheckout(
            str(repo_clone), settings.holdout_dir, branch=config.settings.holdout_branch
        )
        holdout.ensure()
        titles: dict[int, str] = {}
        harnesses: dict[AgentRole, HarnessAdapter]
        http: httpx.AsyncClient | None = None
        if fixture:
            name, items = load_fixture()
            set_id = ensure_eval_set(
                writer, name=name, repo=repo, items=[(i.issue, i.eval_class, None) for i in items]
            )
            keyed = fixture_harness(items)
            harnesses = dict.fromkeys(AgentRole, keyed)
            source = fixture_issue_source(items)
            titles = {i.issue: i.task.name for i in items}
        else:
            if set_name is None:
                warn("name the set with --set, or replay the fixture with --fixture")
                return 2
            found = writer.find_eval_set(set_name)
            if found is None:
                warn(f"no eval set named {set_name!r}; record one with `factory eval add`")
                return 2
            set_id = str(found["id"])
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
            http = httpx.AsyncClient(base_url=GITHUB_API, timeout=30.0)
            minter = InstallationTokenMinter(
                app_id=settings.github_app_id,
                private_key_pem=settings.github_private_key_file.read_text(encoding="utf-8"),
                installation_id=settings.github_installation_id,
                http=http,
            )
            client = GitHubClient(owner=org, repo=repo, tokens=minter, http=http)

            async def from_github(item: EvalItem) -> TaggedIssue:
                return await fetch_issue(
                    client, item.issue_number, task_id=f"eval#{item.issue_number}"
                )

            source = from_github
            if harness_mode == "fake":
                warn("--harness fake needs --fixture; the roster runs a recorded set")
                return 2
            harnesses = {
                role: make_harness(config.roster.role(role), settings) for role in AgentRole
            }
        if reference_pass_rate is not None:
            writer.set_eval_reference(set_id, reference_pass_rate)
        runner = EvalRunner(
            writer=writer,
            settings=settings,
            config=config,
            repo_clone=repo_clone,
            harnesses=harnesses,
            holdout=holdout,
            issues=source,
            factory_version=factory_version(),
        )
        try:
            report = await runner.run(
                set_id,
                trigger=cast(Trigger, trigger),
                trigger_ref=trigger_ref,
                baseline_run_id=baseline,
                titles=titles,
            )
        finally:
            if http is not None:
                await http.aclose()
        print_report(report)
        return 1 if report.blocked else 0
    finally:
        db.close()


def eval_add(
    *,
    set_name: str,
    repo: str,
    items_file: Path,
    reference_pass_rate: float | None,
    data_dir: Path | None,
) -> int:
    """Record a set of closed issues from JSON: ``[{"issue": N, "class": C, "merged_pr": P}]``."""
    try:
        raw = json.loads(items_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        warn(f"cannot read {items_file}: {exc}")
        return 2
    if not isinstance(raw, list) or not all(
        isinstance(i, dict) and "issue" in i and "class" in i for i in raw
    ):
        warn("items must be a JSON list of objects with issue and class (and merged_pr)")
        return 2
    items = [
        (
            int(i["issue"]),
            str(i["class"]),
            None if i.get("merged_pr") is None else int(i["merged_pr"]),
        )
        for i in raw
    ]
    db, writer = open_trace(settings_for(data_dir))
    try:
        try:
            set_id = ensure_eval_set(
                writer,
                name=set_name,
                repo=repo,
                items=items,
                reference_pass_rate=reference_pass_rate,
            )
        except FactoryError as exc:
            warn(f"error ({exc.code}): {exc}")
            return 2
        if reference_pass_rate is not None:
            writer.set_eval_reference(set_id, reference_pass_rate)
        mix = Counter(eval_class for _issue, eval_class, _pr in items)
        classes = ", ".join(f"{k} {v}" for k, v in sorted(mix.items()))
        reference = (
            f"; reference {reference_pass_rate:.1f}" if reference_pass_rate is not None else ""
        )
        total = len(writer.eval_items(set_id))
        say(f"eval set {set_name!r} ({set_id}): {total} items ({classes}){reference}")
        return 0
    finally:
        db.close()


def eval_run(
    *,
    repo_clone: Path,
    org: str,
    repo: str,
    fixture: bool,
    set_name: str | None,
    trigger: str,
    trigger_ref: str | None,
    baseline: str | None,
    data_dir: Path | None,
    harness_mode: str,
    reference_pass_rate: float | None = None,
) -> int:
    try:
        return asyncio.run(
            _run(
                repo_clone=repo_clone,
                org=org,
                repo=repo,
                fixture=fixture,
                set_name=set_name,
                trigger=trigger,
                trigger_ref=trigger_ref,
                baseline=baseline,
                data_dir=data_dir,
                harness_mode=harness_mode,
                reference_pass_rate=reference_pass_rate,
            )
        )
    except FactoryError as exc:
        warn(f"error ({exc.code}): {exc}")
        return 2
