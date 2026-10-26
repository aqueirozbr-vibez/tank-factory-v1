"""CODEOWNERS parsing for governed-path approvals (PRD §Capability 2, TRD §6.2)."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from orchestrator.workspace.git import run_git
from orchestrator.workspace.worktree import default_branch

CODEOWNERS_LOCATIONS = ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS")


@dataclass(frozen=True)
class OwnerRule:
    pattern: str
    owners: tuple[str, ...]


def parse_codeowners(text: str) -> list[OwnerRule]:
    rules: list[OwnerRule] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        rules.append(OwnerRule(parts[0], tuple(parts[1:])))
    return rules


def _to_regex(pattern: str) -> re.Pattern[str]:
    anchored = pattern.startswith("/")
    body = pattern.lstrip("/")
    body = body.rstrip("/")
    has_slash = "/" in body
    out: list[str] = []
    i = 0
    while i < len(body):
        ch = body[i]
        if body.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if body.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        if ch == "*":
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
        i += 1
    core = "".join(out)
    prefix = "^" if (anchored or has_slash) else "^(?:.*/)?"
    suffix = "(?:/.*)?$"
    if body == "":
        return re.compile("^.*$")
    return re.compile(prefix + core + suffix)


def owners_for(path: str, rules: list[OwnerRule]) -> tuple[str, ...]:
    """The owners of ``path``; the last matching rule wins, as GitHub resolves it."""
    normalised = path.lstrip("/")
    winner: tuple[str, ...] = ()
    for rule in rules:
        if _to_regex(rule.pattern).match(normalised):
            winner = rule.owners
    return winner


def is_owner(login: str | None, owners: Sequence[str]) -> bool:
    """``@login`` matches case-insensitively; ``@org/team`` and e-mail owners never match in v1."""
    if not login:
        return False
    wanted = login.lower()
    return any(o.startswith("@") and "/" not in o and o[1:].lower() == wanted for o in owners)


def codeowners_text(repo: Path, *, ref: str | None = None) -> str | None:
    """The CODEOWNERS file at ``ref`` (the default branch when omitted), GitHub's lookup order."""
    rev = ref or default_branch(repo)
    for location in CODEOWNERS_LOCATIONS:
        shown = run_git(["show", f"{rev}:{location}"], cwd=repo, check=False)
        if shown.returncode == 0:
            return shown.stdout
    return None


def load_codeowners(repo: Path, *, ref: str | None = None) -> list[OwnerRule]:
    text = codeowners_text(repo, ref=ref)
    return parse_codeowners(text) if text else []
