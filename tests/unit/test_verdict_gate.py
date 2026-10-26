"""Unit 27: the verdict_consistent gate as a pure function."""

from orchestrator.gates.verdict import normalise_requirement, verdict_consistent_gate
from orchestrator.models.envelopes import Finding, ReviewOutput

ACS = ("AC1", "AC2")


def finding(
    requirement: str,
    *,
    met: bool = True,
    severity: str = "Nit",
    evidence: str = "tests/unit/test_x.py::test_a",
    file: str | None = "src/a.py",
) -> Finding:
    return Finding(
        requirement_id=requirement,
        met=met,
        severity=severity,
        file=file,
        line=3 if file else None,
        problem="Met." if met else "Not met.",
        evidence=evidence,
    )


def envelope(
    *, approved: bool, verdict: str, findings: list[Finding], blocking: list[str] | None = None
) -> ReviewOutput:
    return ReviewOutput(
        approved=approved,
        verdict=verdict,
        findings=findings,
        blocking=blocking or [],
    )


def test_consistent_accepted_envelope_passes() -> None:
    check = verdict_consistent_gate(
        envelope(approved=True, verdict="accepted", findings=[finding("AC1"), finding("ac 2")]),
        ac_ids=ACS,
    )
    assert check.result.passed and check.missing_ac == () and not check.hard
    assert [c.ok for c in check.result.checks] == [True, True, True, True]
    assert normalise_requirement("AC-2") == "AC2" and normalise_requirement("Scope 1") == "Scope 1"


def test_hard_violations() -> None:
    blocker = verdict_consistent_gate(
        envelope(
            approved=True,
            verdict="accepted",
            findings=[finding("AC1", met=False, severity="Blocker"), finding("AC2")],
            blocking=["AC1"],
        ),
        ac_ids=ACS,
    )
    assert not blocker.result.passed and blocker.hard
    assert [v.item for v in blocker.result.violations] == ["approved_with_blocking"]
    assert "AC1" in blocker.result.violations[0].problem

    unmet_major = verdict_consistent_gate(
        envelope(
            approved=True,
            verdict="accepted",
            findings=[finding("AC1", met=False, severity="Major"), finding("AC2")],
        ),
        ac_ids=ACS,
    )
    assert unmet_major.hard  # met=false blocks whatever the severity

    listed = verdict_consistent_gate(
        envelope(
            approved=True,
            verdict="accepted",
            findings=[finding("AC1"), finding("AC2", severity="Minor")],
            blocking=["ac2"],
        ),
        ac_ids=ACS,
    )
    assert listed.hard and "AC2" in listed.result.violations[0].problem

    empty = verdict_consistent_gate(
        envelope(approved=False, verdict="revision_needed", findings=[]), ac_ids=ACS
    )
    assert empty.hard and [v.item for v in empty.result.violations] == ["revision_without_findings"]

    mismatch = verdict_consistent_gate(
        envelope(approved=False, verdict="accepted", findings=[finding("AC1"), finding("AC2")]),
        ac_ids=ACS,
    )
    assert mismatch.hard
    assert [v.item for v in mismatch.result.violations] == ["approved_verdict_mismatch"]


def test_missing_verdicts_are_soft_and_reported() -> None:
    skipped = verdict_consistent_gate(
        envelope(approved=True, verdict="accepted", findings=[finding("AC1")]), ac_ids=ACS
    )
    assert not skipped.result.passed and not skipped.hard and skipped.missing_ac == ("AC2",)
    assert [v.item for v in skipped.result.violations] == ["AC2"]
    assert "missing: AC2" in skipped.result.checks[3].note

    bare = verdict_consistent_gate(
        envelope(
            approved=True,
            verdict="accepted",
            findings=[finding("AC1"), finding("AC2", evidence="", file=None)],
        ),
        ac_ids=ACS,
    )
    assert bare.missing_ac == ("AC2",)  # met without evidence or a location is no verdict

    revision = verdict_consistent_gate(
        envelope(
            approved=False,
            verdict="revision_needed",
            findings=[finding("AC2", met=False, severity="Major")],
            blocking=["AC2"],
        ),
        ac_ids=ACS,
    )
    assert revision.result.passed and revision.missing_ac == ("AC1",)
    assert revision.result.checks[3].ok is False
