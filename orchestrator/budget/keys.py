"""Per-phase virtual keys: issue before the harness runs, settle after (TRD §6.3).

Without a proxy the phase keeps the phase 1 pass-through environment; with
one, the agent sees only the virtual key and the proxy URL, the proxy's
spend becomes the phase cost, and a failed phase at the cap is reported as
``error_max_budget_usd`` so the phases block for budget.
"""

import contextlib
from dataclasses import dataclass, replace

from orchestrator import constants
from orchestrator.budget.proxy import ProxyAdmin, ProxyError, looks_like_cap
from orchestrator.budget.watch import Clock, SpendGuard, utc_clock
from orchestrator.harness.base import HarnessResult
from orchestrator.models.enums import AgentRole
from orchestrator.models.settings import Settings
from orchestrator.trace.writer import TraceWriter

KEY_GRACE_S = 60
PROXY_ENV_KEYS = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "CODEX_API_KEY",
        "CODEX_HOME",
        "FACTORY_PHASE_ID",
    }
)


@dataclass(frozen=True)
class IssuedKey:
    env: dict[str, str]
    key: str | None
    key_id: str | None
    max_budget_usd: float | None

    @property
    def proxied(self) -> bool:
        return self.key is not None


def agent_env_for(
    settings: Settings, *, harness: str, key: str, proxy_url: str, phase_id: str
) -> dict[str, str]:
    """The explicit agent environment: base allow-list, the proxy and one virtual key."""
    env = Settings.subprocess_base_env()
    env["FACTORY_PHASE_ID"] = phase_id
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    if harness == "codex":
        env["OPENAI_BASE_URL"] = f"{proxy_url}/v1"
        env["OPENAI_API_KEY"] = key
        env["CODEX_API_KEY"] = key
        env["CODEX_HOME"] = str(settings.data_dir / "codex-home")
    else:
        env["ANTHROPIC_BASE_URL"] = proxy_url
        env["ANTHROPIC_API_KEY"] = key
    return env


class PhaseKeys:
    def __init__(
        self,
        admin: ProxyAdmin | None = None,
        *,
        task_proxy_url: str | None = None,
        watch_interval_s: float = constants.SPEND_WATCHDOG_INTERVAL_S,
        clock: Clock = utc_clock,
    ) -> None:
        self.admin = admin
        self.task_proxy_url = task_proxy_url
        self.watch_interval_s = watch_interval_s
        self.clock = clock

    def guard(
        self,
        issued: IssuedKey,
        *,
        writer: TraceWriter,
        task_id: str,
        run_id: str,
        phase_id: str,
        phase_cap_usd: float,
    ) -> SpendGuard | None:
        """The spend watchdog for a proxied phase; ``None`` without a proxy (TRD §6.3)."""
        if self.admin is None or issued.key is None:
            return None
        return SpendGuard(
            admin=self.admin,
            key=issued.key,
            writer=writer,
            task_id=task_id,
            run_id=run_id,
            phase_id=phase_id,
            phase_cap_usd=phase_cap_usd,
            interval_s=self.watch_interval_s,
            clock=self.clock,
        )

    @property
    def enabled(self) -> bool:
        return self.admin is not None

    async def issue(
        self,
        *,
        settings: Settings,
        task_id: str,
        phase_id: str,
        role: AgentRole,
        harness: str,
        model: str,
        cap_usd: float,
        timeout_s: int,
    ) -> IssuedKey:
        if self.admin is None:
            return IssuedKey(settings.agent_env(), None, None, None)
        minted = await self.admin.create_key(
            task_id=task_id,
            phase_id=phase_id,
            role=str(role),
            max_budget_usd=cap_usd,
            duration_s=timeout_s + KEY_GRACE_S,
            models=[model],
        )
        env = agent_env_for(
            settings,
            harness=harness,
            key=minted.key,
            proxy_url=self.task_proxy_url or self.admin.base_url,
            phase_id=phase_id,
        )
        return IssuedKey(env, minted.key, minted.key_id, cap_usd)

    async def settle(self, issued: IssuedKey, result: HarnessResult) -> HarnessResult:
        """Read the key's spend, revoke the key, and make the proxy's figures the record."""
        if self.admin is None or issued.key is None:
            return result
        spend: float | None = None
        try:
            spend = (await self.admin.key_info(issued.key)).spend
        except ProxyError:
            spend = None
        with contextlib.suppress(ProxyError):
            await self.admin.delete_key(issued.key)
        if spend is None:
            return result
        usage = replace(result.usage, cost_usd=spend)
        error = result.error
        cap = issued.max_budget_usd
        at_cap = cap is not None and spend >= cap - 1e-9
        if not result.completed and (at_cap or looks_like_cap(result.error)):
            error = (
                f"error_max_budget_usd: proxy cap {cap if cap is not None else 0:.2f} reached"
                f" (spend {spend:.4f}); {result.error or result.outcome}"
            )
        return replace(result, usage=usage, error=error)
