"""Unit 11: worktree workspace on real git repositories."""

import shutil
from pathlib import Path

import pytest

from orchestrator.models.settings import Settings
from orchestrator.workspace.git import GitError, run_git
from orchestrator.workspace.worktree import (
    blob_sha,
    branch_exists,
    changed_files_since,
    commit_all,
    create_worktree,
    default_branch,
    diff_against_base,
    registered_worktrees,
    remove_worktree,
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "product"
    root.mkdir()
    run_git(["init", "-q", "-b", "main"], cwd=root)
    run_git(["config", "user.email", "dev@example.test"], cwd=root)
    run_git(["config", "user.name", "Dev"], cwd=root)
    run_git(["config", "core.autocrlf", "false"], cwd=root)
    (root / "README.md").write_text("# demo\n", encoding="utf-8")
    (root / "specs").mkdir()
    (root / "specs" / ".gitkeep").write_text("", encoding="utf-8")
    run_git(["add", "-A"], cwd=root)
    run_git(["commit", "-q", "-m", "seed"], cwd=root)
    return root


def test_create_commit_diff_and_remove(repo: Path, tmp_path: Path) -> None:
    worktrees = tmp_path / "worktrees"
    wt = create_worktree(repo, issue=418, worktrees_dir=worktrees)
    assert wt.branch == "agent/418"
    assert wt.path.is_dir() and (wt.path / "README.md").is_file()
    assert run_git(["rev-parse", "HEAD"], cwd=wt.path).stdout.strip() == wt.base_sha
    assert run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=wt.path).stdout.strip() == "agent/418"

    assert commit_all(wt.path, "nothing") is None

    (wt.path / "specs" / "418-invoice-due-date.md").write_text("# Spec\n", encoding="utf-8")
    sha = commit_all(wt.path, "spec: 418 invoice due date")
    assert sha is not None and sha != wt.base_sha
    author = run_git(["log", "-1", "--format=%an <%ae>"], cwd=wt.path).stdout.strip()
    settings = Settings()
    assert author == f"{settings.git_author_name} <{settings.git_author_email}>"

    assert "+# Spec" in diff_against_base(wt.path, wt.base_sha)
    assert changed_files_since(wt.path, wt.base_sha) == ["specs/418-invoice-due-date.md"]
    assert len(blob_sha(wt.path, "specs/418-invoice-due-date.md")) == 40

    again = create_worktree(repo, issue=418, worktrees_dir=worktrees)
    assert again.path == wt.path and again.branch == wt.branch

    remove_worktree(repo, wt.path)
    assert not wt.path.exists()
    assert branch_exists(repo, "agent/418")
    assert wt.path not in registered_worktrees(repo)

    reused = create_worktree(repo, issue=418, worktrees_dir=worktrees)
    assert (reused.path / "specs" / "418-invoice-due-date.md").is_file()
    remove_worktree(repo, reused.path)


def test_default_branch_prefers_origin_head(repo: Path, tmp_path: Path) -> None:
    assert default_branch(repo) == "main"
    clone = tmp_path / "clone"
    run_git(["clone", "-q", str(repo), str(clone)], cwd=tmp_path)
    run_git(["config", "core.autocrlf", "false"], cwd=clone)
    assert default_branch(clone) == "main"
    run_git(["checkout", "-q", "-b", "feature"], cwd=clone)
    assert default_branch(clone) == "main"  # origin/HEAD still wins
    wt = create_worktree(clone, issue=7, worktrees_dir=tmp_path / "wts")
    assert wt.base_sha == run_git(["rev-parse", "origin/main"], cwd=clone).stdout.strip()


def test_path_bound_to_another_branch_is_refused(repo: Path, tmp_path: Path) -> None:
    worktrees = tmp_path / "worktrees"
    wt = create_worktree(repo, issue=1, worktrees_dir=worktrees)
    run_git(["checkout", "-q", "-b", "agent/2"], cwd=wt.path)
    with pytest.raises(GitError, match=r"agent/2.*agent/1"):
        create_worktree(repo, issue=1, worktrees_dir=worktrees)


def test_custom_author_and_explicit_base(repo: Path, tmp_path: Path) -> None:
    first = run_git(["rev-parse", "HEAD"], cwd=repo).stdout.strip()
    (repo / "README.md").write_text("# demo v2\n", encoding="utf-8")
    run_git(["commit", "-q", "-am", "v2"], cwd=repo)
    wt = create_worktree(repo, issue=9, worktrees_dir=tmp_path / "w", base=first)
    assert wt.base_sha == first
    assert (wt.path / "README.md").read_text(encoding="utf-8") == "# demo\n"
    (wt.path / "x.txt").write_text("x\n", encoding="utf-8")
    custom = Settings(git_author_name="Robo", git_author_email="robo@acme.test")
    commit_all(wt.path, "x", settings=custom)
    assert run_git(["log", "-1", "--format=%ae"], cwd=wt.path).stdout.strip() == "robo@acme.test"


def test_stale_registration_recreates_the_directory(repo: Path, tmp_path: Path) -> None:
    """A wiped data directory leaves the clone registering a path that no longer exists."""
    worktrees = tmp_path / "worktrees"
    wt = create_worktree(repo, issue=5, worktrees_dir=worktrees)
    (wt.path / "x.txt").write_text("x\n", encoding="utf-8")
    commit_all(wt.path, "x")
    shutil.rmtree(wt.path)
    assert wt.path in registered_worktrees(repo)  # git still lists it, as prunable
    again = create_worktree(repo, issue=5, worktrees_dir=worktrees)
    assert again.path == wt.path and again.path.is_dir() and again.branch == "agent/5"
    assert (again.path / "x.txt").is_file()  # the branch's work survived the prune


def test_a_new_task_starts_from_the_remotes_latest_main(repo: Path, tmp_path: Path) -> None:
    """The factory's clone is never pulled; a task admitted after a merge must still see it."""
    clone = tmp_path / "clone"
    run_git(["clone", "-q", str(repo), str(clone)], cwd=tmp_path)
    run_git(["config", "core.autocrlf", "false"], cwd=clone)
    (repo / "merged.py").write_text("X = 1\n", encoding="utf-8")
    run_git(["add", "-A"], cwd=repo)
    run_git(["commit", "-q", "-m", "a merge after the clone"], cwd=repo)
    upstream = run_git(["rev-parse", "HEAD"], cwd=repo).stdout.strip()
    assert run_git(["rev-parse", "main"], cwd=clone).stdout.strip() != upstream  # local is stale
    wt = create_worktree(clone, issue=7, worktrees_dir=tmp_path / "wts")
    assert wt.base_sha == upstream and (wt.path / "merged.py").is_file()


def test_an_existing_branch_keeps_its_fork_point_as_base(repo: Path, tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    run_git(["clone", "-q", str(repo), str(clone)], cwd=tmp_path)
    run_git(["config", "core.autocrlf", "false"], cwd=clone)
    worktrees = tmp_path / "wts"
    wt = create_worktree(clone, issue=8, worktrees_dir=worktrees)
    (wt.path / "mine.py").write_text("Y = 2\n", encoding="utf-8")
    commit_all(wt.path, "the task's own change")
    (repo / "other.py").write_text("Z = 3\n", encoding="utf-8")
    run_git(["add", "-A"], cwd=repo)
    run_git(["commit", "-q", "-m", "another task merged"], cwd=repo)
    again = create_worktree(clone, issue=8, worktrees_dir=worktrees)
    assert again.base_sha == wt.base_sha
    assert changed_files_since(again.path, again.base_sha) == ["mine.py"]
