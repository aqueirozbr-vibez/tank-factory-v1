"""Workspaces: git plumbing, worktrees (phases 1 and 2), containers (phase 3)."""

from orchestrator.workspace.git import GitError, run_git
from orchestrator.workspace.holdout import HoldoutCheckout
from orchestrator.workspace.worktree import (
    Worktree,
    commit_all,
    create_worktree,
    diff_against_base,
    remove_worktree,
)

__all__ = [
    "GitError",
    "HoldoutCheckout",
    "Worktree",
    "commit_all",
    "create_worktree",
    "diff_against_base",
    "remove_worktree",
    "run_git",
]
