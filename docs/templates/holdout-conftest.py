"""Holdout collection rule for issues that run in parallel (copy to tests/acceptance/conftest.py
on your holdout branch).

The factory copies the whole holdout `tests/acceptance/` folder into each task's worktree to run
the full suite, and several issues are usually in flight at once, each on its own `agent/<n>`
branch cut from `main`. Without this rule, issue 2's acceptance tests fail issue 1's suite
because issue 2's code does not exist on issue 1's branch yet.

Convention for each holdout file:
- name it `test_<issue>_<anything>.py`, for example `test_7_server.py` for issue 7;
- make its first line `# holdout-requires: <the module it tests>`, for example
  `# holdout-requires: mypackage.server`.

Then:
- the task's own file (the issue number in the branch name) always runs, so a missing or
  misnamed module still fails the suite;
- another issue's file runs once its module exists on the branch (that issue has merged);
  until then it is left out.
"""

import importlib.util
import re
import subprocess
from pathlib import Path

ISSUE_FILE = re.compile(r"^test_(\d+)_")
BRANCH = re.compile(r"^agent/(\d+)$")
REQUIRES = re.compile(r"^#\s*holdout-requires:\s*([\w.]+)", re.MULTILINE)


def _own_issue(root: Path) -> int | None:
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    match = BRANCH.match(branch)
    return int(match.group(1)) if match else None


def _built(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except ModuleNotFoundError:
        return False


def pytest_ignore_collect(collection_path: Path) -> bool | None:
    match = ISSUE_FILE.match(collection_path.name)
    if match is None or collection_path.suffix != ".py":
        return None
    if int(match.group(1)) == _own_issue(collection_path.parent):
        return None
    required = REQUIRES.search(collection_path.read_text(encoding="utf-8")[:500])
    if required is None or _built(required.group(1)):
        return None
    return True
