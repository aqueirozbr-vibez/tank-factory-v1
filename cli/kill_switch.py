"""``factory stop`` and ``factory start``: the kill-switch from the VM (PRD §Capability 5).

``stop`` sets ``FACTORY_ENABLED`` to 0 and ends every live process within 60 s;
``start`` re-enables with a reason and re-admits nothing. Both go through the
running orchestrator's ``POST /controls/kill-switch`` so the one trace writer
records them; when no orchestrator answers, the flag is written directly and
any labelled task container is killed.
"""

import asyncio
import getpass
from pathlib import Path
from typing import Any

import httpx

from cli.common import open_trace, say, settings_for, warn
from orchestrator import constants
from orchestrator.models.settings import Settings
from orchestrator.watchdogs import KillSwitch, StopReport
from orchestrator.workspace.container import DockerCli

LOOPBACK_BINDS = frozenset({"", "0.0.0.0", "::", "*"})


def api_url(settings: Settings) -> str:
    host = "127.0.0.1" if settings.bind in LOOPBACK_BINDS else settings.bind
    return f"http://{host}:{settings.port}/controls/kill-switch"


def via_api(settings: Settings, *, enabled: bool, reason: str, actor: str) -> dict[str, Any] | None:
    """The running orchestrator's answer, or ``None`` when nothing listens."""
    try:
        response = httpx.post(
            api_url(settings),
            json={"enabled": int(enabled), "reason": reason},
            headers={"X-Factory-Actor": actor},
            timeout=constants.KILL_SWITCH_BOUND_S + 10,
        )
    except httpx.ConnectError:
        return None
    body: dict[str, Any] = response.json() if response.content else {}
    body["status_code"] = response.status_code
    return body


async def direct(
    settings: Settings, *, enabled: bool, reason: str, actor: str
) -> StopReport | None:
    """No orchestrator is running: flip the flag here and kill any labelled container."""
    db, writer = open_trace(settings)
    try:
        docker = DockerCli(settings.docker_bin) if settings.workspace == "container" else None
        switch = KillSwitch(writer, docker=docker)
        if switch.enabled() == enabled:
            return None
        if enabled:
            return await switch.enable(actor=actor, reason=reason)
        return await switch.disable(actor=actor, reason=reason)
    finally:
        db.close()


def kill_switch(command: str, *, reason: str, actor: str | None, data_dir: Path | None) -> int:
    settings = settings_for(data_dir)
    who = actor or getpass.getuser()
    enabled = command == "start"
    verb = "re-enabled" if enabled else "stopped"
    if not reason.strip():
        warn("a one-line --reason is required; the control event records it")
        return 2
    answer = via_api(settings, enabled=enabled, reason=reason.strip(), actor=who)
    if answer is not None:
        code = int(answer["status_code"])
        if code == 409:
            say(f"factory already {verb}: {answer.get('message', '')}")
            return 0
        if code != 200:
            warn(f"the orchestrator refused ({code}): {answer.get('message', answer)}")
            return 1
        live = int(answer.get("live_processes", 0))
        say(
            f"factory {verb} by {who} (event {answer.get('event_seq')}): "
            f"{len(answer.get('cancelled', []))} task(s) cancelled, "
            f"{len(answer.get('containers', []))} container(s) killed, "
            f"{live} live process(es) after {answer.get('elapsed_s', 0)} s"
        )
        return 0 if live == 0 else 1
    report = asyncio.run(direct(settings, enabled=enabled, reason=reason.strip(), actor=who))
    if report is None:
        say(f"factory already {verb} (no orchestrator is running)")
        return 0
    say(
        f"factory {verb} by {who} without a running orchestrator (event {report.event_seq}):"
        f" {len(report.cancelled)} task(s) marked cancelled, {len(report.containers)} container(s)"
        f" killed, {report.live_after} live process(es)"
    )
    return 0 if report.live_after == 0 else 1
