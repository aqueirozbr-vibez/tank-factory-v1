"""One task, one branch, one worktree (PRD §Capability 1; architecture.md · Workspace)."""

from dataclasses import dataclass
from pathlib import Path

from orchestrator import constants
from orchestrator.models.settings import Settings
from orchestrator.workspace.git import GitError, run_git


@dataclass(frozen=True)
class Worktree:
    repo: Path
    path: Path
    branch: str
    base_sha: str


def branch_for(issue: int) -> str:
    return f"{constants.BRANCH_PREFIX}{issue}"


def default_branch(repo: Path) -> str:
    """``origin/HEAD`` when the clone knows it, else the branch checked out in ``repo``."""
    head = run_git(["symbolic-ref", "-q", "refs/remotes/origin/HEAD"], cwd=repo, check=False)
    if head.returncode == 0 and head.stdout.strip():
        return head.stdout.strip().removeprefix("refs/remotes/origin/")
    return run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo).stdout.strip()


def branch_exists(repo: Path, branch: str) -> bool:
    probe = run_git(["rev-parse", "--verify", "-q", f"refs/heads/{branch}"], cwd=repo, check=False)
    return probe.returncode == 0


def registered_worktrees(repo: Path) -> dict[Path, str]:
    """Worktree path → branch name, from ``git worktree list --porcelain``."""
    output = run_git(["worktree", "list", "--porcelain"], cwd=repo).stdout
    result: dict[Path, str] = {}
    current: Path | None = None
    for line in output.splitlines():
        if line.startswith("worktree "):
            current = Path(line[len("worktree ") :]).resolve()
        elif line.startswith("branch ") and current is not None:
            result[current] = line[len("branch ") :].removeprefix("refs/heads/")
    return result


def _fresh_base(repo: Path, default: str) -> str:
    """The default branch as the remote has it now, so a new task starts from merged work.

    The clone is never pulled, so its local ``main`` stays where it was cloned; a task
    admitted after a merge would otherwise plan and build without the merged code. Falls back
    to the local branch when there is no remote or the fetch fails.
    """
    run_git(["fetch", "-q", "origin", default], cwd=repo, check=False)
    remote = f"origin/{default}"
    probe = run_git(["rev-parse", "--verify", "-q", remote], cwd=repo, check=False)
    return remote if probe.returncode == 0 else default


def _base_sha(repo: Path, branch: str, base_ref: str) -> str:
    """A new branch starts at ``base_ref``; an existing one keeps the point it left from, so
    its diff never shows work merged into the default branch since as its own."""
    if branch_exists(repo, branch):
        fork = run_git(["merge-base", branch, base_ref], cwd=repo, check=False)
        if fork.returncode == 0 and fork.stdout.strip():
            return fork.stdout.strip()
    return run_git(["rev-parse", base_ref], cwd=repo).stdout.strip()


def create_worktree(
    repo: Path,
    *,
    issue: int,
    worktrees_dir: Path,
    base: str | None = None,
    branch: str | None = None,
) -> Worktree:
    branch = branch or branch_for(issue)
    path = (worktrees_dir / f"agent-{issue}").resolve()
    base_ref = base or default_branch(repo)
    if base is None:
        base_ref = _fresh_base(repo, base_ref)
    base_sha = _base_sha(repo, branch, base_ref)
    worktrees_dir.mkdir(parents=True, exist_ok=True)
    existing = registered_worktrees(repo)
    if path in existing:
        if existing[path] != branch:
            raise GitError(f"{path} is a worktree of {existing[path]!r}, not {branch!r}")
        if path.is_dir():
            return Worktree(repo=repo, path=path, branch=branch, base_sha=base_sha)
        # The directory went away under a live registration (a wiped data directory): prune
        # the stale entry and recreate it, so no task ever runs in a path that does not exist.
        run_git(["worktree", "prune"], cwd=repo)
    if branch_exists(repo, branch):
        run_git(["worktree", "add", str(path), branch], cwd=repo)
    else:
        run_git(["worktree", "add", "-b", branch, str(path), base_sha], cwd=repo)
    return Worktree(repo=repo, path=path, branch=branch, base_sha=base_sha)


def remove_worktree(repo: Path, path: Path) -> None:
    """Remove the working directory; the branch stays (PRD §Capability 1 · cancel)."""
    if path.resolve() in registered_worktrees(repo):
        run_git(["worktree", "remove", "--force", str(path)], cwd=repo)
    run_git(["worktree", "prune"], cwd=repo)


def commit_all(path: Path, message: str, *, settings: Settings | None = None) -> str | None:
    """Stage everything and commit as the factory; ``None`` when there is nothing to commit."""
    cfg = settings or Settings()
    run_git(["add", "-A"], cwd=path)
    staged = run_git(["diff", "--cached", "--quiet"], cwd=path, check=False)
    if staged.returncode == 0:
        return None
    run_git(
        [
            "-c",
            f"user.name={cfg.git_author_name}",
            "-c",
            f"user.email={cfg.git_author_email}",
            "commit",
            "-q",
            "-m",
            message,
        ],
        cwd=path,
    )
    return run_git(["rev-parse", "HEAD"], cwd=path).stdout.strip()


def diff_against_base(path: Path, base_sha: str) -> str:
    return run_git(["diff", f"{base_sha}...HEAD"], cwd=path).stdout


def changed_files_since(path: Path, base_sha: str) -> list[str]:
    output = run_git(["diff", "--name-only", f"{base_sha}...HEAD"], cwd=path).stdout
    return [line for line in output.splitlines() if line]


def blob_sha(path: Path, rel: str, *, rev: str = "HEAD") -> str:
    """The blob sha of ``rel`` at ``rev``; approvals bind to the spec's sha (PRD §Cap. 2)."""
    return run_git(["rev-parse", f"{rev}:{rel}"], cwd=path).stdout.strip()
