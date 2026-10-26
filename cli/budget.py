"""``factory budget`` and ``factory dry-run``: the budget controls from the VM (PRD §Cap. 5).

``budget --task T --raise-to USD --reason ...`` is the one-time raise, sent to
the running orchestrator's ``POST /tasks/{id}/budget`` or written to the trace
directly when none answers. ``dry-run --repo R --issues a b c`` replays three
issues, derives the per-task budget at 2x the highest completed cost, records
the derivation in the install log and refuses below two completions.
"""

import asyncio
import getpass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from cli.common import open_trace, say, settings_for, warn
from cli.kill_switch import LOOPBACK_BINDS
from cli.run_spec import _run as run_one
from orchestrator import constants
from orchestrator.budget.controls import raise_task_budget
from orchestrator.budget.dry_run import DryRunIssue, InstallLog, derive_budget
from orchestrator.errors import FactoryError
from orchestrator.models.enums import EventKind
from orchestrator.models.settings import Settings


def budget_url(settings: Settings, task_id: str) -> str:
    host = "127.0.0.1" if settings.bind in LOOPBACK_BINDS else settings.bind
    return f"http://{host}:{settings.port}/tasks/{quote(task_id, safe='')}/budget"


def raise_via_api(
    settings: Settings, *, task_id: str, new_budget: float, reason: str, actor: str
) -> dict[str, Any] | None:
    try:
        response = httpx.post(
            budget_url(settings, task_id),
            json={"budget_usd": new_budget, "reason": reason},
            headers={"X-Factory-Actor": actor},
            timeout=30,
        )
    except httpx.ConnectError:
        return None
    body: dict[str, Any] = response.json() if response.content else {}
    body["status_code"] = response.status_code
    return body


def budget(
    *, task_id: str, raise_to: float, reason: str, actor: str | None, data_dir: Path | None
) -> int:
    settings = settings_for(data_dir)
    who = actor or getpass.getuser()
    if not reason.strip():
        warn("a one-line --reason is required; the control event records it")
        return 2
    answer = raise_via_api(
        settings, task_id=task_id, new_budget=raise_to, reason=reason.strip(), actor=who
    )
    if answer is not None:
        code = int(answer["status_code"])
        if code == 200:
            say(
                f"budget of {task_id} raised from {answer.get('old'):.2f}"
                f" to {answer.get('new'):.2f} by {who} (event {answer.get('event_seq')}); remove"
                f" `{constants.LABEL_NEEDS_HUMAN}` to resume"
            )
            return 0
        warn(f"the orchestrator refused ({code}): {answer.get('message', answer)}")
        return 1
    db, writer = open_trace(settings)
    try:
        outcome = raise_task_budget(
            writer, task_id=task_id, new_budget_usd=raise_to, actor=who, reason=reason.strip()
        )
    finally:
        db.close()
    if not outcome.ok:
        warn(f"refused ({outcome.code}): {outcome.message}")
        return 1
    say(
        f"{outcome.message} by {who} without a running orchestrator (event {outcome.event_seq});"
        f" remove `{constants.LABEL_NEEDS_HUMAN}` to resume"
    )
    return 0


async def _dry_run(
    *,
    repo: Path,
    issues: list[int],
    harness: str | None,
    data_dir: Path | None,
    org: str,
    skip_self_test: bool,
    repo_name: str | None = None,
) -> int:
    repo = repo.resolve()
    settings = settings_for(data_dir or repo / ".factory-dev" / "data")
    results: list[DryRunIssue] = []
    for index, issue in enumerate(issues):
        try:
            outcome = await run_one(
                repo=repo,
                issue=issue,
                spec=None,
                harness_name=harness,
                scripts=None,
                data_dir=settings.data_dir,
                org=org,
                budget_usd=constants.DRY_RUN_PROVISIONAL_BUDGET_USD,
                skip_self_test=skip_self_test or index > 0,
                repo_name=repo_name,
            )
        except FactoryError as exc:
            warn(f"issue {issue}: error ({exc.code}): {exc}")
            results.append(DryRunIssue(issue, False, 0.0, exc.code))
            continue
        results.append(
            DryRunIssue(
                issue,
                outcome.passed,
                outcome.cost_usd,
                None if outcome.blocked_reason is None else str(outcome.blocked_reason),
            )
        )
        state = "completed" if outcome.passed else f"blocked ({outcome.blocked_reason})"
        say(f"issue {issue}: {state} · cost ${outcome.cost_usd:.2f}")
    derivation = derive_budget(results)
    log = InstallLog(settings.data_dir)
    log.append(derivation.record())
    db, writer = open_trace(settings)
    try:
        if derivation.refused or derivation.per_task_budget_usd is None:
            writer.write_event(
                EventKind.LOG, "orchestrator", payload={"dry_run": "refused", **derivation.record()}
            )
            warn(f"dry run refused: {derivation.reason} (see {log.path})")
            return 1
        writer.set_per_task_budget(derivation.per_task_budget_usd, mode="install")
        installation = writer.get_installation()
        daily_cap = float(installation["daily_cap_usd"]) if installation is not None else 0.0
        writer.write_event(
            EventKind.LOG, "orchestrator", payload={"dry_run": "derived", **derivation.record()}
        )
    finally:
        db.close()
    say(
        f"per-task budget ${derivation.per_task_budget_usd:.2f} ({derivation.reason});"
        f" daily cap ${daily_cap:.2f}; recorded in {log.path}"
    )
    return 0


def dry_run(
    *,
    repo: Path,
    issues: list[int],
    harness: str | None,
    data_dir: Path | None,
    org: str,
    skip_self_test: bool,
) -> int:
    if len(issues) != constants.DRY_RUN_ISSUES:
        warn(f"the dry run replays exactly {constants.DRY_RUN_ISSUES} issues")
        return 2
    return asyncio.run(
        _dry_run(
            repo=repo,
            issues=issues,
            harness=harness,
            data_dir=data_dir,
            org=org,
            skip_self_test=skip_self_test,
        )
    )
