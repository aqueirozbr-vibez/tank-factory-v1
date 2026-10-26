"""Git as an explicit subprocess (code-standards.md · Python · Subprocesses)."""

import base64
import subprocess
from collections.abc import Iterable, Sequence
from pathlib import Path

from orchestrator.errors import FactoryError
from orchestrator.models.settings import Settings


class GitError(FactoryError):
    code = "git_error"


def _env() -> dict[str, str]:
    env = Settings.subprocess_base_env()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["LC_ALL"] = "C"
    return env


def run_git(
    args: Sequence[str],
    *,
    cwd: Path,
    input_text: str | None = None,
    check: bool = True,
    timeout_s: float = 120,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            env=_env(),
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitError(f"git {' '.join(args)} could not run: {exc}") from exc
    if check and completed.returncode != 0:
        detail = completed.stderr.strip()[-500:]
        raise GitError(f"git {' '.join(args)} failed ({completed.returncode}): {detail}")
    return completed


def _split_z(output: str) -> list[str]:
    return [part for part in output.split("\0") if part]


def head_sha(cwd: Path) -> str:
    return run_git(["rev-parse", "HEAD"], cwd=cwd).stdout.strip()


def list_files(cwd: Path) -> tuple[list[str], list[str]]:
    """``(tracked, untracked)`` POSIX paths; untracked excludes ignored files."""
    tracked = _split_z(run_git(["ls-files", "-z"], cwd=cwd).stdout)
    untracked = _split_z(
        run_git(["ls-files", "-z", "--others", "--exclude-standard"], cwd=cwd).stdout
    )
    return sorted(set(tracked)), sorted(set(untracked))


def hash_files(cwd: Path, paths: Iterable[str]) -> dict[str, str]:
    """Blob hashes for the files that exist, as git would store them."""
    existing = [p for p in paths if (cwd / p).is_file()]
    if not existing:
        return {}
    output = run_git(
        ["hash-object", "--stdin-paths"], cwd=cwd, input_text="\n".join(existing) + "\n"
    ).stdout
    hashes = output.split()
    if len(hashes) != len(existing):
        raise GitError("git hash-object returned an unexpected number of hashes")
    return dict(zip(existing, hashes, strict=True))


def restore_tracked(cwd: Path, paths: Sequence[str]) -> None:
    """Restore tracked paths from the index (the state before the agent ran)."""
    if paths:
        run_git(["checkout", "--", *paths], cwd=cwd)


def push_argv(remote: str, branch: str, token: str | None) -> list[str]:
    """``git push`` arguments; the App token travels only as a transient extra header."""
    argv: list[str] = []
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode("ascii")
        argv += ["-c", f"http.extraheader=AUTHORIZATION: basic {basic}"]
    return [*argv, "push", remote, f"{branch}:{branch}"]


def push_branch(cwd: Path, branch: str, *, remote: str = "origin", token: str | None = None) -> str:
    """Push ``branch`` to ``remote`` and return the sha that is now on it."""
    run_git(push_argv(remote, branch, token), cwd=cwd, timeout_s=300)
    return head_sha(cwd)
