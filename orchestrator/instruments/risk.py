"""Task risk classification (PRD §Task risk classification; lives in `.factory/risk.yaml`).

The strongest change class the Scope touches, not the sum; a base score
adjusted multiplicatively; the governed class decided by path, never by
score. Tier letters come from the planner's ``risk_inputs.tiers_touched``
and from Scope paths that match a tier's path prefixes (A and E). Nothing
else is inferred from a path: in the PRD's fifth worked example a billing
path the Scope did not name is a tech-lead override, not an automatic
class. The PRD's worked examples are this module's unit fixtures.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from orchestrator import constants
from orchestrator.errors import ConfigError
from orchestrator.gates.spec import ParsedSpec
from orchestrator.models.enums import RiskClass
from orchestrator.models.envelopes import RiskInputs

TIER_ORDER = "ABCDEU"
PATH_TOKEN = re.compile(r"[\w./\-]+")
BULLET = re.compile(r"^\s*(?:[-*]|\d+[.)])\s*")
TIMES = "×"  # noqa: RUF001  the PRD equation glyph


class TierRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    base: float = Field(ge=0)
    paths: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
    destructive_migration: bool = False


class Band(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    thresholds: tuple[int, int]
    multipliers: tuple[float, float, float]

    def apply(self, value: int) -> tuple[int, float]:
        """The band index and multiplier for ``value``."""
        low, high = self.thresholds
        if value <= low:
            return 0, self.multipliers[0]
        if value <= high:
            return 1, self.multipliers[1]
        return 2, self.multipliers[2]


class DependencyRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    none: float = 1.0
    any: float = 1.5


class Thresholds(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    high: float = 50.0
    low_floor: float = 1.0


class GovernedRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tier: str = "A"
    destructive_migration: bool = True
    new_secret: bool = True
    budget_request_over_default_factor: float = 2.0


class RiskInstrument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int
    tiers: dict[str, TierRule]
    size: Band
    blast_radius: Band
    new_dependency: DependencyRule
    thresholds: Thresholds
    governed: GovernedRule

    @classmethod
    def from_yaml(cls, text: str) -> "RiskInstrument":
        try:
            instrument = cls.model_validate(yaml.safe_load(text))
        except (yaml.YAMLError, ValidationError) as exc:
            raise ConfigError(f"risk.yaml is invalid: {exc}", detail={"file": "risk.yaml"}) from exc
        missing = [tier for tier in TIER_ORDER if tier not in instrument.tiers]
        if missing:
            raise ConfigError(f"risk.yaml lacks tiers {missing}", detail={"file": "risk.yaml"})
        return instrument

    @classmethod
    def load(cls, config_dir: Path) -> "RiskInstrument":
        """The instrument published in a product repository's ``.factory/`` directory."""
        path = config_dir / "risk.yaml"
        if not path.is_file():
            raise ConfigError(
                f"risk.yaml is missing from {config_dir}", detail={"file": "risk.yaml"}
            )
        return cls.from_yaml(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Factor:
    name: str
    rule: str
    value: float


@dataclass(frozen=True)
class RiskBreakdown:
    """One classification, with enough detail for the plan comment and the trace."""

    tier: str
    tier_name: str
    base: float
    factors: tuple[Factor, ...]
    score: float
    risk_class: RiskClass | None
    reasons: tuple[str, ...]
    scope_paths: tuple[str, ...]
    existing_paths: tuple[str, ...]
    threshold_high: float
    governed_path: str | None = None
    overridden_by: str | None = None
    override_reason: str | None = None
    instrument_version: int | None = None

    @property
    def unclassifiable(self) -> bool:
        return self.risk_class is None

    @classmethod
    def recorded(
        cls, risk_class: RiskClass, score: float, governed_path: str | None
    ) -> "RiskBreakdown":
        """A breakdown rebuilt from the task row when the full one is gone (restart)."""
        return cls(
            tier="A" if risk_class is RiskClass.GOVERNED else "?",
            tier_name="recorded",
            base=score,
            factors=(),
            score=score,
            risk_class=risk_class,
            reasons=("rebuilt from the task row",),
            scope_paths=(),
            existing_paths=(),
            threshold_high=constants.RISK_HIGH_THRESHOLD,
            governed_path=governed_path,
        )

    def equation(self) -> str:
        if not self.factors:
            return f"{self.score:g} (recorded)"
        parts = f" {TIMES} ".join(f"{factor.value:g}" for factor in self.factors)
        return f"{self.base:g} {TIMES} {parts} = {self.score:g}"

    def conclusion(self) -> str:
        if self.risk_class is RiskClass.GOVERNED:
            return "tier A path: CODEOWNER plan approval before build."
        if self.risk_class is RiskClass.HIGH:
            return f"{self.score:g} >= {self.threshold_high:g}: plan approval before build."
        if self.risk_class is RiskClass.LOW:
            return f"{self.score:g} < {self.threshold_high:g}: build starts unattended."
        return "tier U: no classifiable Scope path; blocked for spec correction."

    def payload(self) -> dict[str, object]:
        return {
            "tier": self.tier,
            "tier_name": self.tier_name,
            "base": self.base,
            "factors": [
                {"name": factor.name, "rule": factor.rule, "value": factor.value}
                for factor in self.factors
            ],
            "score": self.score,
            "risk": None if self.risk_class is None else str(self.risk_class),
            "equation": self.equation(),
            "conclusion": self.conclusion(),
            "reasons": list(self.reasons),
            "scope_paths": list(self.scope_paths),
            "governed_path": self.governed_path,
            "overridden_by": self.overridden_by,
            "override_reason": self.override_reason,
            "instrument_version": self.instrument_version,
        }

    def correction_text(self) -> str:
        return (
            "Risk classification failed: tier U. The Scope must name at least one path that"
            " exists in the repository (a new file counts when its directory exists), and"
            " risk_inputs.tiers_touched must name the change classes touched."
            f" Scope paths seen: {list(self.scope_paths) or 'none'}."
        )


def scope_paths(spec: ParsedSpec) -> tuple[str, ...]:
    """The leading path-like token of every Scope line, in order, without repeats."""
    paths: list[str] = []
    for line in spec.sections.get("Scope", "").splitlines():
        stripped = BULLET.sub("", line).strip().strip("`")
        token = PATH_TOKEN.match(stripped)
        if token and ("/" in token.group(0) or "." in token.group(0)):
            paths.append(token.group(0))
    return tuple(dict.fromkeys(paths))


def _path_exists(worktree: Path | None, rel: str) -> bool:
    """A path counts when it exists or its directory does, so new files are allowed."""
    if worktree is None:
        return True
    target = worktree.joinpath(*rel.strip("/").split("/"))
    return target.exists() or target.parent.exists()


def infer_tiers(instrument: RiskInstrument, paths: Sequence[str]) -> dict[str, str]:
    """Tier letter → the first Scope path that matched one of the tier's path prefixes."""
    found: dict[str, str] = {}
    for letter, rule in instrument.tiers.items():
        for path in paths:
            if any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in rule.paths):
                found.setdefault(letter, path)
    return found


def strongest(tiers: Sequence[str]) -> str:
    return min(tiers, key=TIER_ORDER.index) if tiers else "U"


def classify(
    instrument: RiskInstrument,
    *,
    scope: Sequence[str],
    inputs: RiskInputs,
    worktree: Path | None,
) -> RiskBreakdown:
    """Classify one task; never raises on planner input, tier U is the answer instead."""
    existing = tuple(path for path in scope if _path_exists(worktree, path))
    inferred = infer_tiers(instrument, existing)
    candidates = [
        tier
        for tier in [*inputs.tiers_touched, *inferred]
        if tier in instrument.tiers and tier != "U"
    ]
    reasons = [f"planner: tier {tier}" for tier in inputs.tiers_touched]
    reasons += [f"path {path}: tier {tier}" for tier, path in inferred.items()]
    if not scope:
        reasons.append("Scope names no paths")
    elif not existing:
        reasons.append("no Scope path exists in the repository")
    elif not candidates:
        reasons.append("no tier declared or inferred")
    tier = strongest(candidates) if existing else "U"
    rule = instrument.tiers[tier]
    governed_path: str | None = None
    if tier == instrument.governed.tier:
        governed_path = inferred.get(tier) or (existing[0] if existing else None)

    size_low, size_high = instrument.size.thresholds
    size_rules = (
        f"<= {size_low} lines",
        f"{size_low + 1}-{size_high} lines",
        f"> {size_high} lines",
    )
    blast_low, blast_high = instrument.blast_radius.thresholds
    blast_rules = (
        f"{blast_low} module",
        f"{blast_low + 1}-{blast_high} modules",
        f">= {blast_high + 1} modules",
    )
    modules = max(len(inputs.modules), 1)
    size_index, size_mult = instrument.size.apply(inputs.est_lines)
    blast_index, blast_mult = instrument.blast_radius.apply(modules)
    dependency = instrument.new_dependency
    dep_mult = dependency.any if inputs.new_dependencies else dependency.none
    factors = (
        Factor("Size", f"~{inputs.est_lines} lines ({size_rules[size_index]})", size_mult),
        Factor("Blast radius", f"{modules} ({blast_rules[blast_index]})", blast_mult),
        Factor(
            "New dependency",
            ", ".join(inputs.new_dependencies) if inputs.new_dependencies else "None",
            dep_mult,
        ),
    )
    score = round(rule.base * size_mult * blast_mult * dep_mult, 1)

    risk_class: RiskClass | None
    if tier == "U" or score < instrument.thresholds.low_floor:
        risk_class = None
    elif tier == instrument.governed.tier:
        risk_class = RiskClass.GOVERNED
    elif score >= instrument.thresholds.high:
        risk_class = RiskClass.HIGH
    else:
        risk_class = RiskClass.LOW
    return RiskBreakdown(
        tier=tier,
        tier_name=rule.name,
        base=rule.base,
        factors=factors,
        score=score,
        risk_class=risk_class,
        reasons=tuple(reasons),
        scope_paths=tuple(scope),
        existing_paths=existing,
        threshold_high=instrument.thresholds.high,
        governed_path=governed_path,
        instrument_version=instrument.version,
    )


def override(
    breakdown: RiskBreakdown, risk_class: RiskClass, *, by: str, reason: str
) -> RiskBreakdown:
    """A tech lead's override; the score stays, the class, login and reason are recorded."""
    return RiskBreakdown(
        tier=breakdown.tier,
        tier_name=breakdown.tier_name,
        base=breakdown.base,
        factors=breakdown.factors,
        score=breakdown.score,
        risk_class=risk_class,
        reasons=(*breakdown.reasons, f"override by {by}: {reason}"),
        scope_paths=breakdown.scope_paths,
        existing_paths=breakdown.existing_paths,
        threshold_high=breakdown.threshold_high,
        governed_path=breakdown.governed_path,
        overridden_by=by,
        override_reason=reason,
        instrument_version=breakdown.instrument_version,
    )


def write_risk_header(text: str, risk_class: RiskClass) -> str:
    """Set the ``risk:`` line under the title, replacing the template's placeholder."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip().lower().startswith("risk:"):
            lines[index] = f"risk: {risk_class}"
            return "\n".join(lines) + "\n"
    for index, line in enumerate(lines):
        if line.startswith("# "):
            lines[index + 1 : index + 1] = ["", f"risk: {risk_class}"]
            return "\n".join(lines) + "\n"
    return f"risk: {risk_class}\n\n{text}"
