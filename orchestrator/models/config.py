"""Typed `.factory/` configuration: roster, quality commands, protected paths.

What the PRD forbids is refused here, before any row is written
(PRD §Capability 2, 3, 4; architecture.md · Invariant 9).
"""

from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from orchestrator import constants
from orchestrator.errors import ConfigError
from orchestrator.models.enums import AgentRole

Harness = Literal["claude_code", "codex", "fake"]
Vendor = Literal["anthropic", "openai"]
Thinking = Literal["low", "medium", "high"]

HARNESS_VENDOR: dict[str, Vendor] = {"claude_code": "anthropic", "codex": "openai"}
REVIEWER_TOOLS = frozenset({"Read", "Glob", "Grep"})
PLANNER_WRITES = ("specs/",)


class RoleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    harness: Harness
    model: str = Field(min_length=1, description="pinned model id, recorded on phases.model_id")
    vendor: Vendor | None = None
    thinking: Thinking | None = None
    tools: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    max_turns: int | None = Field(default=None, ge=1)
    phase_cap_usd: float = Field(gt=0)
    timeout_s: int = Field(gt=0)

    @model_validator(mode="after")
    def _derive_vendor(self) -> Self:
        if self.vendor is None:
            derived = HARNESS_VENDOR.get(self.harness)
            if derived is None:
                raise ValueError("the fake harness must name its vendor")
            object.__setattr__(self, "vendor", derived)
        return self

    @property
    def vendor_name(self) -> Vendor:
        assert self.vendor is not None  # set by the validator
        return self.vendor


class Roster(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    planner: RoleConfig
    builder: RoleConfig
    reviewer: RoleConfig

    @model_validator(mode="after")
    def _prd_rules(self) -> Self:
        if self.builder.vendor_name == self.reviewer.vendor_name:
            raise ValueError(
                "builder and reviewer share one vendor"
                f" ({self.builder.vendor_name}); the roster must keep two vendors (PRD Cap. 4)"
            )
        if self.reviewer.writes:
            raise ValueError("the reviewer writes nothing; roster reviewer.writes must be empty")
        extra_tools = set(self.reviewer.tools) - REVIEWER_TOOLS
        if extra_tools:
            raise ValueError(
                f"the reviewer may use only Read, Glob, Grep; found {sorted(extra_tools)}"
            )
        if tuple(self.planner.writes) != PLANNER_WRITES:
            raise ValueError(
                "the planner writes only under specs/; roster planner.writes must be ['specs/']"
            )
        if not self.builder.writes:
            raise ValueError("the builder needs a write boundary; roster builder.writes is empty")
        return self

    def role(self, role: AgentRole) -> RoleConfig:
        return {
            AgentRole.PLANNER: self.planner,
            AgentRole.BUILDER: self.builder,
            AgentRole.REVIEWER: self.reviewer,
        }[role]

    def vendors(self) -> dict[str, str]:
        return {
            "planner": self.planner.vendor_name,
            "builder": self.builder.vendor_name,
            "reviewer": self.reviewer.vendor_name,
        }


class QualityCommands(BaseModel):
    """Argv lists; never a shell string (code-standards.md · Python)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    format: tuple[str, ...] = Field(min_length=1)
    lint: tuple[str, ...] = Field(min_length=1)
    typecheck: tuple[str, ...] = Field(min_length=1)
    test: tuple[str, ...] = Field(min_length=1, description="focused test ids are appended")
    full_suite: tuple[str, ...] = Field(min_length=1)

    def ordered(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """The gate order the PRD fixes: format, lint, typecheck, focused tests."""
        return (
            ("format", self.format),
            ("lint", self.lint),
            ("typecheck", self.typecheck),
            ("focused_tests", self.test),
        )


class FactorySettings(BaseModel):
    """`factory.yaml`: installation-level defaults an installation may set."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    concurrency_n: int = Field(
        default=constants.CONCURRENCY_DEFAULT,
        ge=constants.CONCURRENCY_MIN,
        le=constants.CONCURRENCY_MAX,
    )
    protected_paths: tuple[str, ...] = Field(default=(), description="additions to the PRD set")
    holdout_branch: str | None = Field(
        default=None, description="branch holding tests/acceptance; the default branch when unset"
    )
    dependency_manifests: tuple[str, ...] = constants.DEPENDENCY_MANIFESTS
    install_command: str | None = Field(
        default=None,
        description="baked into the task image at build; uv sync --frozen when uv.lock exists",
    )


class FactoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    root: Path
    config_sha: str = Field(min_length=64, max_length=64)
    main_commit: str
    roster: Roster
    quality: QualityCommands
    settings: FactorySettings
    protected_paths: tuple[str, ...]
    risk_yaml: str
    review_policy_yaml: str

    def config_version_row(self) -> dict[str, str]:
        """Keyword arguments for ``TraceWriter.load_config_version``."""
        from orchestrator.trace.writer import dump_json

        return {
            "config_sha": self.config_sha,
            "main_commit": self.main_commit,
            "roster_json": self.roster.model_dump_json(),
            "risk_yaml": self.risk_yaml,
            "review_policy_yaml": self.review_policy_yaml,
            "protected_paths_json": dump_json(list(self.protected_paths)),
            "quality_commands_json": self.quality.model_dump_json(),
            "vendors_json": dump_json(self.roster.vendors()),
        }

    def prompt_override_dir(self, role: AgentRole) -> Path | None:
        candidate = self.root / "prompts" / str(role)
        return candidate if candidate.is_dir() else None


def validation_error(exc: ValidationError, source: str) -> ConfigError:
    """Flatten a pydantic error into one declarative message."""
    problems = "; ".join(
        f"{'.'.join(str(p) for p in err['loc']) or source}: {err['msg']}" for err in exc.errors()
    )
    return ConfigError(f"{source} is invalid: {problems}", detail={"source": source})
