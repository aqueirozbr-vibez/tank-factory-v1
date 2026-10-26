"""Who may act: a GitHub login's repository permission, cached (PRD §Capability 1; TRD §6.2)."""

from orchestrator import constants
from orchestrator.intake.ports import GitHubPort
from orchestrator.trace.writer import TraceWriter


async def actor_permission(
    writer: TraceWriter, github: GitHubPort, login: str | None, *, task_id: str
) -> str:
    """``none`` for bots and anonymous actors; otherwise GitHub's answer, cached five minutes."""
    if not login or login.endswith("[bot]"):
        return "none"
    cached = writer.cached_permission(login, ttl_s=constants.PERMISSION_CACHE_S)
    if cached is not None:
        return cached
    permission = await github.collaborator_permission(login, task_id=task_id)
    writer.cache_permission(login, permission)
    return permission


def has_write(permission: str) -> bool:
    return permission in constants.WRITE_PERMISSIONS
