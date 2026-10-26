"""Unit 21: the holdout checkout."""

from pathlib import Path

import pytest

from orchestrator.workspace.git import run_git
from orchestrator.workspace.holdout import HoldoutCheckout, HoldoutError


def test_holdout_checkout_lifecycle(product: Path, tmp_path: Path) -> None:
    data = tmp_path / "data"
    holdout = HoldoutCheckout(str(product), data / "holdout")
    assert holdout.ensure() == data / "holdout"
    assert holdout.ensure() == data / "holdout"  # idempotent
    assert holdout.test_ids() == ["tests/acceptance/test_holdout.py::test_h"]
    assert holdout.has_test("tests/acceptance/test_holdout.py::test_h")
    assert holdout.has_test("tests/acceptance/test_holdout.py")
    assert not holdout.has_test("tests/acceptance/test_holdout.py::test_missing")
    assert not holdout.has_test("tests/unit/test_a.py::test_a")  # only the acceptance dir counts

    (product / "tests" / "acceptance" / "test_more.py").write_text(
        "def test_new_case() -> None:\n    assert True\n\n\n"
        "async def test_async_case() -> None:\n    assert True\n",
        encoding="utf-8",
    )
    run_git(["add", "-A"], cwd=product)
    run_git(["commit", "-q", "-m", "more holdout"], cwd=product)
    head = holdout.refresh()
    assert head == run_git(["rev-parse", "HEAD"], cwd=product).stdout.strip()
    assert holdout.test_ids() == [
        "tests/acceptance/test_holdout.py::test_h",
        "tests/acceptance/test_more.py::test_new_case",
        "tests/acceptance/test_more.py::test_async_case",
    ]

    workspace = tmp_path / "ws"
    (workspace / "src").mkdir(parents=True)
    destination = holdout.copy_into(workspace)
    assert destination == workspace / "tests" / "acceptance"
    assert (destination / "test_more.py").is_file()
    assert sorted(p.name for p in workspace.iterdir()) == ["src", "tests"]
    assert not (workspace / ".git").exists()

    holdout.assert_outside([tmp_path / "worktrees", product])
    with pytest.raises(HoldoutError, match="lies under"):
        HoldoutCheckout(
            str(product), tmp_path / "worktrees" / "agent-1" / "holdout"
        ).assert_outside([tmp_path / "worktrees"])


def test_explicit_branch_and_missing_dir(product: Path, tmp_path: Path) -> None:
    run_git(["branch", "holdout", "main"], cwd=product)
    holdout = HoldoutCheckout(str(product), tmp_path / "h", branch="holdout")
    holdout.refresh()
    assert (
        run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=holdout.path).stdout.strip() == "holdout"
    )
    empty = HoldoutCheckout(str(product), tmp_path / "h")  # same path, tests dir exists
    assert empty.test_ids()
    bare = HoldoutCheckout(str(product), tmp_path / "nowhere")
    assert bare.test_ids() == []
    with pytest.raises(HoldoutError, match="no tests/acceptance"):
        bare.copy_into(tmp_path / "ws2")
