"""The spec gate: is the planner's file a complete contract? (PRD §Capability 2)."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from orchestrator.gates.base import Check, GateResult, Violation
from orchestrator.models.enums import GateName

SECTIONS = (
    "Outcome",
    "Scope",
    "Non-goals",
    "Acceptance criteria",
    "Constraints",
    "Open questions",
    "Test plan",
)
HEADING = re.compile(r"^##\s+(.+?)\s*$")
TITLE = re.compile(r"^#\s+(.+?)\s*$")
AC_LINE = re.compile(r"^\s*(\d+)\.\s+(.*)$")
EARS = re.compile(r"^\s*WHEN\s+.+?\s+THE SYSTEM SHALL\s+\S.*$", re.IGNORECASE | re.DOTALL)
PLAN_LINE = re.compile(r"^\s*[-*]?\s*AC\s*(\d+)\b(?P<rest>.*)$", re.IGNORECASE)
TEST_ID = re.compile(r"tests/[\w./\-]+(?:::[\w\[\]\-,]+)?")
ADR_REF = re.compile(r"docs/adr/[\w.\-]+\.md")
NO_ADR = "No ADR applies."
HOLDOUT_PREFIX = "tests/acceptance/"
HasTest = Callable[[str], bool]


@dataclass(frozen=True)
class ParsedSpec:
    title: str | None
    sections: dict[str, str]
    order: tuple[str, ...]
    criteria: tuple[tuple[int, str], ...]
    plan: dict[int, tuple[str, ...]]
    open_questions: tuple[str, ...]

    def holdout_ids_for(self, ac: int) -> tuple[str, ...]:
        return tuple(t for t in self.plan.get(ac, ()) if t.startswith(HOLDOUT_PREFIX))


def _criteria(text: str) -> tuple[tuple[int, str], ...]:
    items: list[tuple[int, list[str]]] = []
    for line in text.splitlines():
        match = AC_LINE.match(line)
        if match:
            items.append((int(match.group(1)), [match.group(2).strip()]))
        elif items and line.strip():
            items[-1][1].append(line.strip())
    return tuple((number, " ".join(parts)) for number, parts in items)


def _plan(text: str) -> dict[int, tuple[str, ...]]:
    plan: dict[int, list[str]] = {}
    for line in text.splitlines():
        match = PLAN_LINE.match(line)
        if not match:
            continue
        ids = [m.group(0).rstrip(".,;)") for m in TEST_ID.finditer(match.group("rest"))]
        plan.setdefault(int(match.group(1)), []).extend(ids)
    return {ac: tuple(dict.fromkeys(ids)) for ac, ids in plan.items()}


def _questions(text: str) -> tuple[str, ...]:
    questions: list[str] = []
    for line in text.splitlines():
        stripped = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line).strip()
        if not stripped or stripped.rstrip(".").lower() in ("none", "nenhuma", "nenhum"):
            continue
        questions.append(stripped)
    return tuple(questions)


def parse_spec(text: str) -> ParsedSpec:
    title: str | None = None
    sections: dict[str, list[str]] = {}
    order: list[str] = []
    current: str | None = None
    for line in text.splitlines():
        if title is None and TITLE.match(line):
            title = TITLE.match(line).group(1)  # type: ignore[union-attr]
            continue
        heading = HEADING.match(line)
        if heading:
            current = heading.group(1)
            if current not in sections:
                sections[current] = []
                order.append(current)
            continue
        if current is not None:
            sections[current].append(line)
    cleaned = {name: "\n".join(lines).strip() for name, lines in sections.items()}
    return ParsedSpec(
        title=title,
        sections=cleaned,
        order=tuple(order),
        criteria=_criteria(cleaned.get("Acceptance criteria", "")),
        plan=_plan(cleaned.get("Test plan", "")),
        open_questions=_questions(cleaned.get("Open questions", "")),
    )


# --- the four gates ----------------------------------------------------------------


def spec_sections(spec: ParsedSpec) -> GateResult:
    checks = [
        Check(name, name in spec.sections and bool(spec.sections[name]), "") for name in SECTIONS
    ]
    violations = [
        Violation(c.item, "section missing" if c.item not in spec.sections else "section empty")
        for c in checks
        if not c.ok
    ]
    present = [s for s in spec.order if s in SECTIONS]
    if not violations and present != list(SECTIONS):
        violations.append(Violation("order", "sections are not in the template order"))
    return GateResult(GateName.SPEC_SECTIONS, not violations, tuple(checks), tuple(violations))


def spec_ears(spec: ParsedSpec) -> GateResult:
    if not spec.criteria:
        return GateResult(
            GateName.SPEC_EARS,
            False,
            (Check("criteria", False, "none numbered"),),
            (Violation("Acceptance criteria", "no numbered criterion found"),),
        )
    checks = [
        Check(f"AC{number}", bool(EARS.match(text)), text[:80]) for number, text in spec.criteria
    ]
    violations = [
        Violation(c.item, "not in the form WHEN <condition> THE SYSTEM SHALL <behaviour>")
        for c in checks
        if not c.ok
    ]
    return GateResult(GateName.SPEC_EARS, not violations, tuple(checks), tuple(violations))


def spec_ac_holdout_map(spec: ParsedSpec, has_test: HasTest) -> tuple[GateResult, list[str]]:
    """Every criterion maps to at least one holdout id that exists; returns the missing ids too."""
    checks: list[Check] = []
    violations: list[Violation] = []
    missing: list[str] = []
    for number, _ in spec.criteria:
        holdout_ids = spec.holdout_ids_for(number)
        if not holdout_ids:
            checks.append(Check(f"AC{number}", False, "no holdout test id in the Test plan"))
            violations.append(
                Violation(
                    f"AC{number}", "no holdout test id (tests/acceptance/...) in the Test plan"
                )
            )
            continue
        existing = [t for t in holdout_ids if has_test(t)]
        if existing:
            checks.append(Check(f"AC{number}", True, ", ".join(existing)))
        else:
            checks.append(
                Check(f"AC{number}", False, "holdout test not found: " + ", ".join(holdout_ids))
            )
            violations.append(
                Violation(
                    f"AC{number}", "mapped holdout test does not exist in the holdout checkout"
                )
            )
            missing.extend(holdout_ids)
    if not spec.criteria:
        checks.append(Check("criteria", False, "none to map"))
        violations.append(Violation("Test plan", "no criteria to map"))
    return GateResult(
        GateName.SPEC_AC_HOLDOUT_MAP, not violations, tuple(checks), tuple(violations)
    ), list(dict.fromkeys(missing))


def spec_adr_cited(spec: ParsedSpec, worktree: Path) -> GateResult:
    constraints = spec.sections.get("Constraints", "")
    if NO_ADR in constraints:
        return GateResult(GateName.SPEC_ADR_CITED, True, (Check(NO_ADR, True, "stated"),))
    refs = ADR_REF.findall(constraints)
    if not refs:
        return GateResult(
            GateName.SPEC_ADR_CITED,
            False,
            (Check("Constraints", False, "no ADR reference"),),
            (Violation("Constraints", f'cite a docs/adr/*.md file or state exactly "{NO_ADR}"'),),
        )
    checks = [Check(ref, (worktree / Path(*ref.split("/"))).is_file(), "") for ref in refs]
    violations = [Violation(c.item, "cited ADR file does not exist") for c in checks if not c.ok]
    return GateResult(GateName.SPEC_ADR_CITED, not violations, tuple(checks), tuple(violations))


@dataclass(frozen=True)
class SpecGateOutcome:
    spec: ParsedSpec
    results: tuple[GateResult, ...]
    missing_holdout: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def first_failure(self) -> GateResult | None:
        return next((r for r in self.results if not r.passed), None)

    @property
    def open_questions(self) -> tuple[str, ...]:
        return self.spec.open_questions


def run_spec_gates(text: str, *, has_test: HasTest, worktree: Path) -> SpecGateOutcome:
    spec = parse_spec(text)
    sections = spec_sections(spec)
    ears = spec_ears(spec)
    mapping, missing = spec_ac_holdout_map(spec, has_test)
    adr = spec_adr_cited(spec, worktree)
    return SpecGateOutcome(spec, (sections, ears, mapping, adr), tuple(missing))
