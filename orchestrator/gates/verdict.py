"""``verdict_consistent``: the reviewer's envelope agrees with itself (PRD §Capability 4).

Hard violations re-prompt once and then block for a human; the soft one
(an acceptance criterion with no evidenced verdict) re-prompts once and
then code forces the verdict to ``revision_needed``.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from orchestrator.gates.base import Check, GateResult, Violation, failed, passed
from orchestrator.models.enums import GateName
from orchestrator.models.envelopes import Finding, ReviewOutput

HARD_ITEMS = frozenset(
    {"approved_with_blocking", "revision_without_findings", "approved_verdict_mismatch"}
)
AC_ID = re.compile(r"^\s*ac\s*[-_ ]?\s*(\d+)\s*$", re.IGNORECASE)


def normalise_requirement(requirement_id: str) -> str:
    """``ac 2`` / ``AC-2`` / ``ac2`` → ``AC2``; anything else is returned stripped."""
    match = AC_ID.match(requirement_id)
    return f"AC{match.group(1)}" if match else requirement_id.strip()


def is_blocking(finding: Finding) -> bool:
    return finding.severity == "Blocker" or not finding.met


def has_evidence(finding: Finding) -> bool:
    return bool(finding.evidence.strip()) or finding.file is not None


@dataclass(frozen=True)
class VerdictCheck:
    result: GateResult
    missing_ac: tuple[str, ...]

    @property
    def hard(self) -> bool:
        return any(v.item in HARD_ITEMS for v in self.result.violations)


def verdict_consistent_gate(envelope: ReviewOutput, *, ac_ids: Sequence[str]) -> VerdictCheck:
    violations: list[Violation] = []
    blocking_ids = {normalise_requirement(r) for r in envelope.blocking}
    blockers = [
        normalise_requirement(f.requirement_id)
        for f in envelope.findings
        if is_blocking(f) or normalise_requirement(f.requirement_id) in blocking_ids
    ]
    blockers += [r for r in blocking_ids if r not in blockers]
    if envelope.approved and blockers:
        violations.append(
            Violation(
                "approved_with_blocking",
                "approved is true but these findings block: " + ", ".join(sorted(set(blockers))),
            )
        )
    if envelope.verdict == "revision_needed" and not envelope.findings:
        violations.append(
            Violation("revision_without_findings", "revision_needed needs at least one finding")
        )
    if envelope.approved != (envelope.verdict == "accepted"):
        violations.append(
            Violation(
                "approved_verdict_mismatch",
                f"approved={str(envelope.approved).lower()} does not match verdict"
                f" {envelope.verdict}",
            )
        )
    covered = {
        normalise_requirement(f.requirement_id) for f in envelope.findings if has_evidence(f)
    }
    missing = tuple(ac for ac in ac_ids if ac not in covered)
    if missing and envelope.verdict == "accepted":
        violations += [
            Violation(ac, "no met verdict with evidence (a test id or file:line)") for ac in missing
        ]
    checks = [
        Check("approved consistent with findings", not (envelope.approved and blockers)),
        Check(
            "revision has findings",
            not (envelope.verdict == "revision_needed" and not envelope.findings),
        ),
        Check("approved matches verdict", envelope.approved == (envelope.verdict == "accepted")),
        Check(
            "every acceptance criterion has a verdict",
            not missing,
            "missing: " + ", ".join(missing) if missing else f"{len(ac_ids)} covered",
        ),
    ]
    if violations:
        return VerdictCheck(failed(GateName.VERDICT_CONSISTENT, violations, checks), missing)
    return VerdictCheck(passed(GateName.VERDICT_CONSISTENT, checks), missing)
