"""Unit 52: ``factory eval add`` records a set with its reference; the report prints the gap."""

import json
from pathlib import Path

import pytest

from cli.eval_cmd import print_report
from cli.main import main
from orchestrator.eval import EvalReport
from orchestrator.trace import TraceDB, TraceWriter


def test_eval_add_records_items_and_the_reference(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    items = [{"issue": 418, "class": "bug", "merged_pr": 431}, {"issue": 419, "class": "docs"}]
    file = tmp_path / "set.json"
    file.write_text(json.dumps(items), encoding="utf-8")
    data = ["--data-dir", str(tmp_path)]
    add = ["eval", "add", "--set", "atlas-90d", "--repo", "atlas-api", "--items", str(file)]
    assert main([*add, "--reference-pass-rate", "80", *data]) == 0
    out = capsys.readouterr().out
    assert "2 items (bug 1, docs 1)" in out and "reference 80.0" in out
    assert main([*add, *data]) == 0  # idempotent: same set, same items, the reference stays
    db = TraceDB.open(tmp_path / "trace.db")
    try:
        writer = TraceWriter(db)
        found = writer.find_eval_set("atlas-90d")
        assert found is not None and found["reference_pass_rate"] == 80.0
        rows = writer.eval_items(str(found["id"]))
        assert [(r["issue_number"], r["class"], r["merged_pr"]) for r in rows] == [
            (418, "bug", 431),
            (419, "docs", None),
        ]
        assert db.scalar("SELECT COUNT(*) FROM eval_sets") == 1
    finally:
        db.close()
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([{"issue": 1, "class": "chore"}]), encoding="utf-8")
    assert main(["eval", "add", "--set", "x", "--repo", "r", "--items", str(bad), *data]) == 2
    assert "eval class" in capsys.readouterr().err
    missing = str(tmp_path / "missing.json")
    assert main(["eval", "add", "--set", "x", "--repo", "r", "--items", missing, *data]) == 2
    shapeless = tmp_path / "shapeless.json"
    shapeless.write_text(json.dumps({"issue": 1}), encoding="utf-8")
    assert main(["eval", "add", "--set", "x", "--repo", "r", "--items", str(shapeless), *data]) == 2


def test_report_prints_the_gap_against_the_reference(capsys: pytest.CaptureFixture[str]) -> None:
    def report(gap: float) -> EvalReport:
        return EvalReport(
            "r1",
            "s1",
            70.0,
            {"bug": 50.0},
            None,
            None,
            None,
            False,
            (),
            reference_pass_rate=round(70.0 - gap, 2),
            gap_points=gap,
        )

    print_report(report(-4.0))
    assert "gap -4.0 points against the reference 74.0 (within 10)" in capsys.readouterr().out
    print_report(report(-12.5))
    assert "(wider than 10)" in capsys.readouterr().out
    print_report(report(3.0))
    assert "gap +3.0 points" in capsys.readouterr().out
    print_report(EvalReport("r1", "s1", 70.0, {}, None, None, None, False, ()))
    assert "gap" not in capsys.readouterr().out
