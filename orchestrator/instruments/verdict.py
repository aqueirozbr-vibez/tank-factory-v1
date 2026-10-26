"""Review verdict policy (PRD §Review verdict policy; lives in `.factory/review-policy.yaml`).

Each finding is scored by severity and adjusted for its spec link, its
evidence and its scope; the blocking score is the strongest adjusted
finding, not the sum; conformance is 100 minus it. ``human_decision_needed``
is a class, not a score. The PRD's worked examples are this module's unit
fixtures and take precedence over the prose where the two differ.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from orchestrator import constants
from orchestrator.errors import ConfigError
from orchestrator.gates.spec import ParsedSpec
from orchestrator.gates.verdict import normalise_requirement
from orchestrator.instruments.risk import TIMES, scope_paths
from orchestrator.models.enums import EventKind
from orchestrator.models.envelopes import Finding, ReviewOutput, Verdict
from orchestrator.trace.writer import TraceWriter

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
AC_OR_SCOPE = re.compile(r"^(AC\d+|scope\s*\d+)$", re.IGNORECASE)
CONSTRAINT = re.compile(r"constraint|docs/adr/|\badr\b", re.IGNORECASE)
REPRODUCTION = re.compile(r"::|\bpytest\b|\breproduc|\bfail(?:s|ed|ing)?\b|^\$ ", re.I | re.M)
SECURITY = re.compile(r"\bsecret|credential|password|api[ _-]?key|\btoken\b|injection|bypass", re.I)
NON_GOAL_CITE = re.compile(r"non-?\s*goals?(?:\s*(\d+))?", re.IGNORECASE)
WORD = re.compile(r"[a-z][a-z_]{3,}")
MIN_STEM = 5


class SpecLinkRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ac_or_scope: float = 1.0
    constraint_or_adr: float = 0.9
    none: float = 0.5


class EvidenceRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    location_and_reproduction: float = 1.0
    location_only: float = 0.8
    assertion_only: float = 0.4


class ScopeRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    inside: float = 1.0
    non_goals: float = 0.0


class PolicyThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    accept_conformance: float = 70.0
    revision_cap: int = constants.REVISION_CAP


class ReviewPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int
    severities: dict[str, float]
    spec_link: SpecLinkRule
    evidence: EvidenceRule
    scope: ScopeRule
    thresholds: PolicyThresholds
    human_always: tuple[str, ...] = ()
    test_integrity: dict[str, str] = {}

    @classmethod
    def from_yaml(cls, text: str) -> "ReviewPolicy":
        try:
            policy = cls.model_validate(yaml.safe_load(text))
        except (yaml.YAMLError, ValidationError) as exc:
            raise ConfigError(
                f"review-policy.yaml is invalid: {exc}", detail={"file": "review-policy.yaml"}
            ) from exc
        missing = [
            s
            for s in ("Blocker", "Major", "Minor", "Nit", "Unverifiable")
            if s not in policy.severities
        ]
        if missing:
            raise ConfigError(
                f"review-policy.yaml lacks severities {missing}",
                detail={"file": "review-policy.yaml"},
            )
        return policy

    @classmethod
    def load(cls, config_dir: Path) -> "ReviewPolicy":
        path = config_dir / "review-policy.yaml"
        if not path.is_file():
            raise ConfigError(
                f"review-policy.yaml is missing from {config_dir}",
                detail={"file": "review-policy.yaml"},
            )
        return cls.from_yaml(path.read_text(encoding="utf-8"))


# --- the diff and the spec ------------------------------------------------------------


def diff_lines(diff: str) -> dict[str, set[int]]:
    """New-side line numbers covered by the hunks, per file (context and added lines)."""
    files: dict[str, set[int]] = {}
    current: str | None = None
    new_line = 0
    in_hunk = False
    for raw in diff.splitlines():
        if raw.startswith("diff --git"):
            current, in_hunk = None, False
            continue
        if raw.startswith("+++ "):
            path = raw[4:].strip()
            current = None if path == "/dev/null" else path.removeprefix("b/")
            if current is not None:
                files.setdefault(current, set())
            in_hunk = False
            continue
        if raw.startswith("--- "):
            continue
        hunk = HUNK.match(raw)
        if hunk:
            new_line, in_hunk = int(hunk.group(1)), True
            continue
        if not in_hunk or current is None or raw.startswith("\\"):
            continue
        if raw.startswith("-"):
            continue
        files[current].add(new_line)
        new_line += 1
    return files


def non_goal_lines(spec: ParsedSpec) -> tuple[str, ...]:
    lines: list[str] = []
    for raw in spec.sections.get("Non-goals", "").splitlines():
        text = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", raw).strip()
        if text and text.lower().rstrip(".") not in ("none", "nenhuma", "nenhum"):
            lines.append(text)
    return tuple(lines)


def stems(text: str) -> set[str]:
    """Words of five letters or more; anything shorter is too weak to dismiss a finding on."""
    return {word for word in WORD.findall(text.lower()) if len(word) >= MIN_STEM}


def _overlap(a: set[str], b: set[str]) -> bool:
    return any(x.startswith(y) or y.startswith(x) for x in a for y in b)


def non_goal_match(finding: Finding, spec: ParsedSpec) -> str | None:
    """The Non-goals line the finding is about, or ``None``."""
    lines = non_goal_lines(spec)
    if not lines:
        return None
    own_text = " ".join(
        [finding.requirement_id, finding.problem, finding.correction, finding.evidence]
    )
    # a citation lives in the requirement id or the evidence; prose that merely
    # mentions Non-goals ("outside Scope and Non-goals") is not one
    cite = NON_GOAL_CITE.search(finding.requirement_id) or NON_GOAL_CITE.search(finding.evidence)
    if cite:
        number = cite.group(1)
        if number and 1 <= int(number) <= len(lines):
            return lines[int(number) - 1]
    rest = " ".join(text for name, text in spec.sections.items() if name != "Non-goals")
    rest_stems = stems(rest)
    finding_stems = stems(own_text)
    for line in lines:
        distinctive = {s for s in stems(line) if not _overlap({s}, rest_stems)}
        if distinctive and _overlap(finding_stems, distinctive):
            return line
    return lines[0] if NON_GOAL_CITE.search(finding.requirement_id) else None


# --- scoring -------------------------------------------------------------------------


@dataclass(frozen=True)
class FindingScore:
    finding: Finding
    tier: str
    base: float
    spec_link: float
    evidence: float
    scope: float
    score: float
    dismissed_by: str | None
    in_diff: bool | None
    notes: tuple[str, ...]

    @property
    def requirement(self) -> str:
        return normalise_requirement(self.finding.requirement_id)

    @property
    def location(self) -> str | None:
        if self.finding.file is None:
            return None
        return (
            f"{self.finding.file}:{self.finding.line}" if self.finding.line else self.finding.file
        )

    def equation(self) -> str:
        return (
            f"{self.base:g} {TIMES} {self.spec_link:g} {TIMES} {self.evidence:g}"
            f" {TIMES} {self.scope:g} = {self.score:g}"
        )

    def payload(self) -> dict[str, object]:
        return {
            "requirement_id": self.requirement,
            "severity": self.finding.severity,
            "tier": self.tier,
            "location": self.location,
            "problem": self.finding.problem,
            "equation": self.equation(),
            "score": self.score,
            "base": self.base,
            "spec_link": self.spec_link,
            "evidence": self.evidence,
            "scope": self.scope,
            "dismissed_by": self.dismissed_by,
            "in_diff": self.in_diff,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class VerdictScore:
    policy_version: int
    verdict: Verdict
    reviewer_verdict: Verdict
    conformance: float
    blocking_score: float
    strongest: FindingScore | None
    scores: tuple[FindingScore, ...]
    dismissed: tuple[FindingScore, ...]
    human_reasons: tuple[str, ...]
    missing_ac: tuple[str, ...]
    revisions_so_far: int
    overridden_by: str | None = None
    override_reason: str | None = None
    accept_conformance: float = 70.0  # the policy threshold the verdict was judged against

    @property
    def accepted(self) -> bool:
        return self.verdict == "accepted"

    def actionable_findings(self) -> list[Finding]:
        """What a revision round is fed: scored findings that were not dismissed."""
        return [s.finding for s in self.scores if s.dismissed_by is None]

    def payload(self) -> dict[str, object]:
        return {
            "policy_version": self.policy_version,
            "verdict": self.verdict,
            "reviewer_verdict": self.reviewer_verdict,
            "conformance": self.conformance,
            "blocking_score": self.blocking_score,
            "strongest": None if self.strongest is None else self.strongest.payload(),
            "findings": [s.payload() for s in self.scores],
            "dismissed": [s.payload() for s in self.dismissed],
            "human_reasons": list(self.human_reasons),
            "missing_ac": list(self.missing_ac),
            "revisions_so_far": self.revisions_so_far,
            "overridden_by": self.overridden_by,
            "override_reason": self.override_reason,
            "accept_conformance": self.accept_conformance,
        }


def spec_link_factor(policy: ReviewPolicy, finding: Finding, spec: ParsedSpec) -> float:
    requirement = normalise_requirement(finding.requirement_id)
    if AC_OR_SCOPE.match(requirement) or requirement in scope_paths(spec):
        return policy.spec_link.ac_or_scope
    if CONSTRAINT.search(requirement):
        return policy.spec_link.constraint_or_adr
    return policy.spec_link.none


def evidence_factor(policy: ReviewPolicy, finding: Finding, *, located: bool) -> float:
    if not located:
        return policy.evidence.assertion_only
    if REPRODUCTION.search(finding.evidence):
        return policy.evidence.location_and_reproduction
    return policy.evidence.location_only


def score_finding(
    policy: ReviewPolicy, finding: Finding, *, spec: ParsedSpec, lines: dict[str, set[int]]
) -> FindingScore:
    notes: list[str] = []
    located = finding.file is not None and finding.line is not None
    in_diff: bool | None = None
    tier = finding.severity
    if located:
        assert finding.file is not None
        in_diff = finding.line in lines.get(finding.file, set())
        if not in_diff:
            tier = "Unverifiable"
            notes.append(f"cited line {finding.file}:{finding.line} is not in the diff")
    base = policy.severities[tier]
    dismissed_by = non_goal_match(finding, spec)
    scope = policy.scope.non_goals if dismissed_by else policy.scope.inside
    if dismissed_by:
        notes.append(f"dismissed by Non-goals: {dismissed_by}")
    spec_link = spec_link_factor(policy, finding, spec)
    evidence = evidence_factor(policy, finding, located=located)
    score = round(base * spec_link * evidence * scope, 1)
    return FindingScore(
        finding, tier, base, spec_link, evidence, scope, score, dismissed_by, in_diff, tuple(notes)
    )


def score_review(
    policy: ReviewPolicy,
    envelope: ReviewOutput,
    *,
    spec: ParsedSpec,
    diff: str,
    missing_ac: Sequence[str] = (),
    revisions_so_far: int = 0,
) -> VerdictScore:
    lines = diff_lines(diff)
    scored = [
        score_finding(policy, f, spec=spec, lines=lines) for f in envelope.findings if not f.met
    ]
    active = tuple(s for s in scored if s.dismissed_by is None)
    dismissed = tuple(s for s in scored if s.dismissed_by is not None)
    strongest = max(active, key=lambda s: s.score) if active else None
    blocking = strongest.score if strongest is not None else 0.0
    conformance = round(100.0 - blocking, 1)
    human: list[str] = []
    if envelope.verdict == "human_decision_needed":
        human.append("reviewer: human_decision_needed")
    for score in active:
        finding = score.finding
        text = " ".join([finding.requirement_id, finding.problem, finding.impact])
        if finding.severity == "Blocker" and SECURITY.search(text):
            human.append(f"security_blocker: {score.requirement}")
    verdict: Verdict
    if human:
        verdict = "human_decision_needed"
    elif conformance >= policy.thresholds.accept_conformance and not missing_ac:
        verdict = "accepted"
    else:
        verdict = "revision_needed"
    if verdict == "revision_needed" and revisions_so_far >= constants.REVISION_CAP:
        verdict = "human_decision_needed"
        human.append("third_revision")
    return VerdictScore(
        policy_version=policy.version,
        verdict=verdict,
        reviewer_verdict=envelope.verdict,
        conformance=conformance,
        blocking_score=blocking,
        strongest=strongest,
        scores=active,
        dismissed=dismissed,
        human_reasons=tuple(human),
        missing_ac=tuple(missing_ac),
        revisions_so_far=revisions_so_far,
        accept_conformance=policy.thresholds.accept_conformance,
    )


def override_verdict(
    score: VerdictScore, verdict: Verdict, *, by: str, reason: str
) -> VerdictScore:
    """The tech lead's call in the monthly audit; the computed score stays on record."""
    return replace(score, verdict=verdict, overridden_by=by, override_reason=reason)


def record_verdict_override(
    writer: TraceWriter,
    *,
    task_id: str,
    score: VerdictScore,
    verdict: Verdict,
    by: str,
    reason: str,
    reviewer_model: str,
    run_id: str | None = None,
) -> VerdictScore:
    overridden = override_verdict(score, verdict, by=by, reason=reason)
    writer.write_event(
        EventKind.AUDIT,
        by,
        task_id=task_id,
        run_id=run_id,
        payload={
            "override": overridden.payload(),
            "computed_verdict": score.verdict,
            "reviewer_model": reviewer_model,
            "policy_version": score.policy_version,
        },
    )
    return overridden
