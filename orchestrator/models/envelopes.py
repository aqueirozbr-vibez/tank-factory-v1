"""The envelopes agents return and the one code returns to them (PRD §Cap. 2 to 4).

Each model exports the JSON schema the harness is given; the harness
validates against it and code validates again here (architecture.md ·
Invariant 3). ``extra="forbid"`` makes the schema closed.
"""

from pathlib import PurePosixPath
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from orchestrator import constants
from orchestrator.models.enums import AgentRole, OutputType

Status = Literal["success", "fail"]
Severity = Literal["Blocker", "Major", "Minor", "Nit", "Unverifiable"]
Verdict = Literal["accepted", "revision_needed", "human_decision_needed"]
Tier = Literal["A", "B", "C", "D", "E", "U"]


def _check_relative(paths: list[str], field: str) -> list[str]:
    for raw in paths:
        pure = PurePosixPath(raw)
        if (
            not raw
            or pure.is_absolute()
            or raw.startswith("\\")
            or ".." in pure.parts
            or ":" in raw
        ):
            raise ValueError(f"{field} entry {raw!r} must be a relative path inside the workspace")
    return paths


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @classmethod
    def json_schema(cls) -> dict[str, object]:
        """The schema passed to the harness (``--json-schema``)."""
        return cls.model_json_schema()


class RiskInputs(Envelope):
    tiers_touched: list[Tier] = Field(default_factory=list)
    est_lines: int = Field(ge=0)
    new_dependencies: list[str] = Field(default_factory=list)
    modules: list[str] = Field(default_factory=list)


class PlanOutput(Envelope):
    status: Status
    summary: str = Field(min_length=1)
    artifacts: list[str] = Field(min_length=1)
    notes_for_next_agent: str = ""
    risk_inputs: RiskInputs
    open_questions: list[str] = Field(default_factory=list)

    @field_validator("artifacts")
    @classmethod
    def _artifacts_relative(cls, value: list[str]) -> list[str]:
        return _check_relative(value, "artifacts")

    @model_validator(mode="after")
    def _includes_spec(self) -> Self:
        if not any(a.startswith("specs/") and a.endswith(".md") for a in self.artifacts):
            raise ValueError("artifacts must include the spec path (specs/<issue>-<slug>.md)")
        return self


class BuildOutput(Envelope):
    status: Status
    summary: str = Field(min_length=1)
    changed_files: list[str] = Field(default_factory=list)
    tests_added: list[str] = Field(default_factory=list)
    commit_message: str = Field(min_length=1)
    notes_for_next_agent: str = ""

    @field_validator("changed_files", "tests_added")
    @classmethod
    def _paths_relative(cls, value: list[str], info: object) -> list[str]:
        field = getattr(info, "field_name", "path")
        return _check_relative(value, str(field))


class Finding(Envelope):
    requirement_id: str = Field(min_length=1, description="AC id, Scope item, Constraint or ADR")
    met: bool
    severity: Severity
    file: str | None = None
    line: int | None = Field(default=None, ge=1)
    problem: str = Field(min_length=1)
    impact: str = ""
    correction: str = ""
    evidence: str = ""


class ReviewOutput(Envelope):
    approved: bool
    verdict: Verdict
    findings: list[Finding] = Field(default_factory=list)
    blocking: list[str] = Field(default_factory=list)


class Check(Envelope):
    item: str
    ok: bool
    note: str = ""


class VerifyOutput(Envelope):
    """What code tells the builder after a failed gate (PRD §Capability 3 · Repair)."""

    status: Literal["pass", "fail"]
    gate: str
    checks: list[Check] = Field(default_factory=list)
    violations: list[str] = Field(default_factory=list)
    command: str | None = None
    exit_code: int | None = None
    output_tail: str = ""

    @field_validator("output_tail")
    @classmethod
    def _cap_tail(cls, value: str) -> str:
        limit = constants.OUTPUT_TAIL_CHARS
        return value if len(value) <= limit else value[-limit:]


ENVELOPE_MODELS: dict[OutputType, type[Envelope]] = {
    OutputType.PLAN: PlanOutput,
    OutputType.BUILD: BuildOutput,
    OutputType.REVIEW: ReviewOutput,
    OutputType.VERIFY: VerifyOutput,
}

ROLE_OUTPUT: dict[AgentRole, OutputType] = {
    AgentRole.PLANNER: OutputType.PLAN,
    AgentRole.BUILDER: OutputType.BUILD,
    AgentRole.REVIEWER: OutputType.REVIEW,
}


def envelope_model_for(role: AgentRole) -> type[Envelope]:
    return ENVELOPE_MODELS[ROLE_OUTPUT[role]]
