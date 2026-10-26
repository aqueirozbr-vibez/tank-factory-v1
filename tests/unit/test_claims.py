"""Unit 09: claim gates."""

from pathlib import Path

from orchestrator.gates.claims import (
    artifacts_exist,
    claim_gates,
    diff_matches_claims,
    files_non_empty,
)
from orchestrator.gates.permissions import PathChange
from orchestrator.models.enums import GateName


def _write(root: Path, rel: str, content: str) -> None:
    target = root / Path(*rel.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def test_all_claims_hold(tmp_path: Path) -> None:
    _write(tmp_path, "src/app.py", "x = 1\n")
    _write(tmp_path, "tests/unit/test_app.py", "def test() -> None:\n    assert True\n")
    changes = [PathChange("src/app.py", "modified"), PathChange("tests/unit/test_app.py", "added")]
    outcome = claim_gates(tmp_path, changes, ["src/app.py", "tests/unit/test_app.py", "src/app.py"])
    assert outcome.passed and outcome.first_failure is None
    assert [r.gate for r in outcome.results] == [
        GateName.ARTIFACTS_EXIST,
        GateName.FILES_NON_EMPTY,
        GateName.DIFF_MATCHES_CLAIMS,
    ]
    for result in outcome.results:
        assert [c.item for c in result.checks] == ["src/app.py", "tests/unit/test_app.py"]
        assert all(c.ok for c in result.checks)


def test_missing_claimed_file(tmp_path: Path) -> None:
    _write(tmp_path, "src/app.py", "x = 1\n")
    result = artifacts_exist(tmp_path, ["src/app.py", "src/gone.py"])
    assert not result.passed
    assert [v.item for v in result.violations] == ["src/gone.py"]
    assert [(c.item, c.ok) for c in result.checks] == [("src/app.py", True), ("src/gone.py", False)]


def test_empty_claimed_file(tmp_path: Path) -> None:
    _write(tmp_path, "src/app.py", "x = 1\n")
    _write(tmp_path, "src/blank.py", "   \n\n")
    (tmp_path / "src" / "zero.bin").write_bytes(b"")
    (tmp_path / "src" / "binary.bin").write_bytes(b"\x00\xff\x00")
    result = files_non_empty(
        tmp_path, ["src/app.py", "src/blank.py", "src/zero.bin", "src/binary.bin"]
    )
    assert not result.passed
    assert [v.item for v in result.violations] == ["src/blank.py", "src/zero.bin"]
    assert result.violations[0].problem == "claimed file is empty"


def test_unchanged_claim_fails_and_unclaimed_change_is_noted() -> None:
    changes = [PathChange("src/app.py", "modified"), PathChange("src/other.py", "added")]
    result = diff_matches_claims(changes, ["src/app.py", "src/untouched.py"])
    assert not result.passed
    assert [v.item for v in result.violations] == ["src/untouched.py"]
    notes = {c.item: c.note for c in result.checks}
    assert notes["src/app.py"] == "modified"
    assert notes["src/untouched.py"] == "not in git status"
    assert notes["src/other.py"] == "added but not claimed"


def test_first_failure_is_in_order(tmp_path: Path) -> None:
    _write(tmp_path, "src/blank.py", "\n")
    outcome = claim_gates(tmp_path, [], ["src/blank.py"])
    assert not outcome.passed
    first = outcome.first_failure
    assert first is not None and first.gate is GateName.FILES_NON_EMPTY
    assert [r.passed for r in outcome.results] == [True, False, False]


def test_no_claims_pass(tmp_path: Path) -> None:
    outcome = claim_gates(tmp_path, [PathChange("a.py", "added")], [])
    assert outcome.passed
    assert outcome.results[0].checks[0].item == "no artifacts claimed"
    assert outcome.results[2].checks[0].note == "added but not claimed"
