"""Process settings read once from the environment (code-standards.md · Python).

No other module reads ``os.environ``. Everything that belongs to the
installation (budgets, concurrency, retention) lives in the ``installation``
row of the trace, not here.
"""

import os
from pathlib import Path
from typing import Final, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]

# The only keys a subprocess inherits from this process; everything else an
# agent needs is set explicitly by the phase (architecture.md · Invariant 5).
SUBPROCESS_ENV_ALLOW_LIST: Final[tuple[str, ...]] = (
    "PATH",
    "HOME",
    "USERPROFILE",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "TEMP",
    "TMP",
    "LANG",
    "LC_ALL",
    "PYTHONIOENCODING",
)

# Phase 1 only: with no model proxy yet, a local real-harness run needs the vendor
# key in the agent environment. Phase 3 replaces this with the proxy virtual key
# (architecture.md · Invariant 5).
AGENT_ENV_PASSTHROUGH: Final[tuple[str, ...]] = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
    "CODEX_HOME",
    "OPENAI_BASE_URL",
)


def load_env_file(path: Path) -> list[str]:
    """Load ``KEY=VALUE`` lines from a local, gitignored ``.env`` into the environment.

    Existing variables win; comments and blank lines are skipped; surrounding
    quotes are stripped. Returns the keys that were set. This is the second and
    last sanctioned write to ``os.environ`` (the first is pydantic-settings).
    """
    if not path.is_file():
        return []
    loaded: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def _default_hooks_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "deploy" / "hooks"


class Settings(BaseSettings):
    """Environment-sourced settings with the ``FACTORY_`` prefix."""

    model_config = SettingsConfigDict(env_prefix="FACTORY_", extra="ignore", frozen=True)

    enabled: bool = Field(
        default=True,
        description="FACTORY_ENABLED kill-switch flag; 0 stops every live phase (PRD Cap. 5).",
    )
    data_dir: Path = Field(
        default=Path("/var/lib/factory"),
        description="Data volume: trace.db, holdout checkout, worktrees, image cache.",
    )
    log_level: LogLevel = "INFO"
    claude_bin: str = Field(
        default="claude", description="Claude Code CLI binary (FACTORY_CLAUDE_BIN)."
    )
    claude_bare: bool = Field(
        default=True, description="Pass --bare to Claude Code (FACTORY_CLAUDE_BARE)."
    )
    codex_bin: str = Field(default="codex", description="Codex CLI binary (FACTORY_CODEX_BIN).")
    git_author_name: str = Field(default="factory", description="Commit author for code steps.")
    git_author_email: str = Field(default="factory@localhost", description="Commit author email.")
    webhook_secret: str | None = Field(default=None, description="GitHub webhook HMAC secret.")
    trusted_proxy_header: str | None = Field(
        default=None,
        description="Header carrying the client IP behind the edge, e.g. X-Forwarded-For.",
    )
    trust_loopback: bool = Field(
        default=False, description="Accept webhooks from loopback (dev only)."
    )
    timezone: str = Field(default="America/Sao_Paulo", description="Installation time zone.")
    github_app_id: str | None = Field(
        default=None, description="GitHub App id (FACTORY_GITHUB_APP_ID)."
    )
    github_installation_id: int | None = Field(
        default=None, description="App installation id on the product repository."
    )
    github_private_key_file: Path | None = Field(
        default=None,
        description="PEM file with the App private key; read at start, never logged.",
    )
    proxy_url: str | None = Field(
        default=None,
        description=(
            "LiteLLM proxy base URL (FACTORY_PROXY_URL); when set, every agent phase gets"
            " a per-phase virtual key and no vendor key."
        ),
    )
    proxy_master_key: str | None = Field(
        default=None,
        description="LiteLLM master key (FACTORY_PROXY_MASTER_KEY); never reaches an agent.",
    )
    workspace: Literal["worktree", "container"] = Field(
        default="worktree",
        description=(
            "Where agent phases run (FACTORY_WORKSPACE): a git worktree on the host, or a"
            " Docker container per task from phase 3."
        ),
    )
    task_image: str | None = Field(
        default=None, description="Task image tag override (FACTORY_TASK_IMAGE)."
    )
    docker_bin: str = Field(default="docker", description="Docker CLI (FACTORY_DOCKER_BIN).")
    docker_network: str = Field(
        default="factory-tasks",
        description="Internal Docker network for task containers (FACTORY_DOCKER_NETWORK).",
    )
    proxy_url_in_tasks: str = Field(
        default="http://proxy:4000",
        description="The proxy URL a task container uses (FACTORY_PROXY_URL_IN_TASKS).",
    )
    hooks_dir: Path = Field(
        default_factory=_default_hooks_dir,
        description="The PreToolUse hook bundle (FACTORY_HOOKS_DIR); read-only in containers.",
    )
    bind: str = Field(default="127.0.0.1", description="API bind address (FACTORY_BIND).")
    port: int = Field(default=8000, description="API port (FACTORY_PORT).")
    service_token: str | None = Field(
        default=None,
        description="Console service token (FACTORY_SERVICE_TOKEN): the console's calls"
        " carry it with X-Factory-Actor.",
    )
    export_key_file: Path | None = Field(
        default=None,
        description="Ed25519 export key (FACTORY_EXPORT_KEY_FILE); factory install writes it.",
    )
    slack_webhook_url: str | None = Field(
        default=None,
        description="Slack incoming webhook (FACTORY_SLACK_WEBHOOK_URL); notification only.",
    )

    @property
    def trace_db_path(self) -> Path:
        """The single trace database file (TRD §4)."""
        return self.data_dir / "trace.db"

    @property
    def console_db_path(self) -> Path:
        """The console's own sessions database, never opened by the orchestrator."""
        return self.data_dir / "console.db"

    @property
    def worktrees_dir(self) -> Path:
        return self.data_dir / "worktrees"

    @property
    def holdout_dir(self) -> Path:
        return self.data_dir / "holdout"

    @staticmethod
    def subprocess_base_env() -> dict[str, str]:
        """The allow-listed part of this process's environment for subprocesses.

        This is the one sanctioned read of ``os.environ`` outside pydantic-settings;
        no secret can pass through it because no secret key is on the list.
        """
        return {key: os.environ[key] for key in SUBPROCESS_ENV_ALLOW_LIST if key in os.environ}

    @staticmethod
    def agent_env() -> dict[str, str]:
        """The environment an agent phase receives in worktree mode (phases 1 and 2)."""
        env = Settings.subprocess_base_env()
        env.update({key: os.environ[key] for key in AGENT_ENV_PASSTHROUGH if key in os.environ})
        env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
        return env
