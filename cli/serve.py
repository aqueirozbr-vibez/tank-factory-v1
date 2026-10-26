"""``factory serve``: run the orchestrator against one GitHub repository."""

import asyncio
import signal
from pathlib import Path

import uvicorn

from cli.common import say, settings_for, warn
from orchestrator.errors import FactoryError
from orchestrator.service import build_service


async def _serve(
    *,
    repo_clone: Path,
    org: str,
    repo: str,
    budget_usd: float,
    harness: str,
    scripts_dir: Path | None,
    data_dir: Path | None,
    bind: str | None,
    port: int | None,
    with_poller: bool,
    poll_interval_s: float | None,
) -> int:
    settings = settings_for(data_dir)
    built = await build_service(
        settings,
        repo_clone=repo_clone,
        org=org,
        repo=repo,
        per_task_budget_usd=budget_usd,
        harness_mode=harness,
        scripts_dir=scripts_dir,
        with_poller=with_poller,
        poll_interval_s=poll_interval_s,
    )
    service = built.service
    requeued = service.start()
    if requeued:
        say(f"restart sweep requeued {len(requeued)} task(s): {', '.join(requeued)}")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_stop(*_: object) -> None:
        loop.call_soon_threadsafe(stop.set)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_stop)
        except (NotImplementedError, RuntimeError):  # Windows
            signal.signal(sig, request_stop)
    host = bind or settings.bind
    listen = port or settings.port
    server = uvicorn.Server(
        uvicorn.Config(service.api, host=host, port=listen, log_level="warning")
    )

    async def stopper() -> None:
        await stop.wait()
        server.should_exit = True

    say(
        f"factory serving {org}/{repo} from {repo_clone} on http://{host}:{listen}"
        f" (harness {harness}; poller {'on' if with_poller else 'off'})"
    )
    try:
        await asyncio.gather(service.run(stop), server.serve(), stopper())
    finally:
        await service.drain()
        await built.http.aclose()
        built.db.close()
    say("factory stopped")
    return 0


def serve(
    *,
    repo_clone: Path,
    org: str,
    repo: str,
    budget_usd: float,
    harness: str,
    scripts_dir: Path | None,
    data_dir: Path | None,
    bind: str | None,
    port: int | None,
    with_poller: bool,
    poll_interval_s: float | None,
) -> int:
    try:
        return asyncio.run(
            _serve(
                repo_clone=repo_clone,
                org=org,
                repo=repo,
                budget_usd=budget_usd,
                harness=harness,
                scripts_dir=scripts_dir,
                data_dir=data_dir,
                bind=bind,
                port=port,
                with_poller=with_poller,
                poll_interval_s=poll_interval_s,
            )
        )
    except (ValueError, FactoryError) as exc:
        warn(str(exc))
        return 2
