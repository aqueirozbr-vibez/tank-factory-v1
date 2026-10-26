"""The holdout checkout: acceptance tests no agent workspace ever contains (PRD §Cap. 3, 5)."""

import re
import shutil
from collections.abc import Iterable
from pathlib import Path

from orchestrator.errors import FactoryError
from orchestrator.workspace.git import run_git

TEST_DEF = re.compile(r"^\s*(?:async\s+)?def\s+(test_\w+)\s*\(", re.MULTILINE)
ACCEPTANCE_DIR = ("tests", "acceptance")


class HoldoutError(FactoryError):
    code = "holdout_error"


class HoldoutCheckout:
    def __init__(self, origin: str, path: Path, *, branch: str | None = None) -> None:
        self.origin = origin
        self.path = path
        self.branch = branch

    @property
    def tests_dir(self) -> Path:
        return self.path.joinpath(*ACCEPTANCE_DIR)

    def assert_outside(self, forbidden: Iterable[Path]) -> None:
        """The checkout must never sit under a workspace directory (Invariant 11)."""
        resolved = self.path.resolve()
        for directory in forbidden:
            root = directory.resolve()
            if resolved == root or root in resolved.parents:
                raise HoldoutError(f"holdout checkout {resolved} lies under workspace root {root}")

    def ensure(self) -> Path:
        if not (self.path / ".git").exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            args = ["clone", "-q"]
            if self.branch:
                args += ["--branch", self.branch]
            run_git([*args, self.origin, str(self.path)], cwd=self.path.parent)
            run_git(["config", "core.autocrlf", "false"], cwd=self.path)
        return self.path

    def _target_branch(self) -> str:
        if self.branch:
            return self.branch
        head = run_git(
            ["symbolic-ref", "-q", "refs/remotes/origin/HEAD"], cwd=self.path, check=False
        )
        if head.returncode == 0 and head.stdout.strip():
            return head.stdout.strip().removeprefix("refs/remotes/origin/")
        return run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=self.path).stdout.strip()

    def refresh(self) -> str:
        """Fetch and hard-reset to origin before the holdout is used; return the head sha."""
        self.ensure()
        run_git(["fetch", "-q", "origin"], cwd=self.path)
        run_git(["reset", "-q", "--hard", f"origin/{self._target_branch()}"], cwd=self.path)
        return run_git(["rev-parse", "HEAD"], cwd=self.path).stdout.strip()

    def test_ids(self) -> list[str]:
        if not self.tests_dir.is_dir():
            return []
        ids: list[str] = []
        for file in sorted(self.tests_dir.rglob("test_*.py")):
            rel = file.relative_to(self.path).as_posix()
            for match in TEST_DEF.finditer(file.read_text(encoding="utf-8")):
                ids.append(f"{rel}::{match.group(1)}")
        return ids

    def has_test(self, test_id: str) -> bool:
        file_part, _, name = test_id.partition("::")
        target = self.path / Path(*file_part.split("/"))
        if not target.is_file() or not file_part.startswith("tests/acceptance/"):
            return False
        if not name:
            return True
        return any(
            m.group(1) == name for m in TEST_DEF.finditer(target.read_text(encoding="utf-8"))
        )

    def copy_into(self, workspace: Path) -> Path:
        """Copy only tests/acceptance into a workspace for the full-suite phase."""
        if not self.tests_dir.is_dir():
            raise HoldoutError(f"no tests/acceptance in the holdout checkout {self.path}")
        destination = workspace.joinpath(*ACCEPTANCE_DIR)
        shutil.copytree(self.tests_dir, destination, dirs_exist_ok=True)
        return destination
