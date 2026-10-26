#!/usr/bin/env python3
"""The factory's PreToolUse hook (PRD §Capability 3; TRD §7.3).

Claude Code runs it before ``Bash``, ``Write``, ``Edit``, ``MultiEdit`` and
``NotebookEdit`` with the call as JSON on stdin. It denies, with exit 2 and
the reason on stderr: ``git push`` and ``git commit`` (the orchestrator
pushes and commits), ``gh``, ``curl`` and ``wget`` (a task reaches only the
model proxy), ``rm -rf``, and any write to a protected path. Standard
library only, so it runs inside the task image and on the host alike; it
fails closed when it cannot read or parse its input. The post-run diff
remains the enforcement for whatever a hook cannot see.
"""

import json
import os
import posixpath
import re
import shlex
import sys
from collections.abc import Sequence

PREFIX = "factory-hook: denied:"
PROTECTED_ENV = "FACTORY_PROTECTED_PATHS"
SEPARATOR = ";"
DEFAULT_PROTECTED: tuple[str, ...] = (
    ".factory/",
    ".claude/",
    ".github/workflows/",
    "tests/acceptance/",
    "CODEOWNERS",
    "uv.lock",
    "poetry.lock",
    "Pipfile.lock",
    "requirements.txt",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "Cargo.lock",
    "go.sum",
    "Gemfile.lock",
)
FILE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})
NETWORK_PROGRAMS = {
    "curl": "curl is not available to agents; a task reaches only the model proxy",
    "wget": "wget is not available to agents; a task reaches only the model proxy",
    "gh": "gh is not available to agents; the orchestrator opens and updates the pull request",
}
GIT_DENIED = {
    "push": "git push is not allowed; the orchestrator pushes the branch after the gates",
    "commit": "git commit is not allowed; the orchestrator commits with your commit_message",
}
GIT_TREE_REWRITERS = frozenset(
    {
        "checkout",
        "restore",
        "switch",
        "apply",
        "am",
        "mv",
        "rm",
        "stash",
        "reset",
        "clean",
        "revert",
        "cherry-pick",
        "rebase",
        "merge",
        "pull",
    }
)
GIT_VALUE_OPTIONS = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env"}
)
MUTATORS = frozenset(
    {
        "cp",
        "mv",
        "rm",
        "rmdir",
        "mkdir",
        "touch",
        "truncate",
        "install",
        "ln",
        "chmod",
        "chown",
        "chgrp",
        "patch",
        "shred",
        "unlink",
        "rsync",
        "tee",
        "dd",
    }
)
WRAPPERS = frozenset(
    {
        "sudo",
        "doas",
        "env",
        "command",
        "exec",
        "nohup",
        "nice",
        "time",
        "timeout",
        "xargs",
        "busybox",
        "stdbuf",
    }
)
SHELLS = frozenset({"sh", "bash", "dash", "zsh", "ksh", "ash"})
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
ABSOLUTE = re.compile(r"^(?:[A-Za-z]:)?/")
MAX_DEPTH = 5


# --- paths ----------------------------------------------------------------------------


def protected_match(path: str, pattern: str) -> bool:
    """The gate's rule: ``**`` matches all; a trailing ``/`` is a prefix; else path or basename."""
    if pattern == "**":
        return True
    if pattern.endswith("/"):
        return path.startswith(pattern) or path == pattern.rstrip("/")
    return path == pattern or path.rsplit("/", 1)[-1] == pattern


def first_protected(path: str, protected: Sequence[str]) -> str | None:
    for pattern in protected:
        if protected_match(path, pattern):
            return pattern
    return None


def normalise(raw: str, cwd: str) -> str | None:
    """``raw`` as a POSIX path relative to the workspace ``cwd``; ``None`` when outside it."""
    text = raw.strip().replace("\\", "/")
    base = cwd.strip().replace("\\", "/").rstrip("/")
    if ABSOLUTE.match(text):
        if text.lower() == base.lower():
            return ""
        if not text.lower().startswith(base.lower() + "/"):
            return None
        text = text[len(base) + 1 :]
    if not text:
        return ""
    text = posixpath.normpath(text)
    if text == "..":
        return None
    if text.startswith("../"):
        return None
    return "" if text == "." else text


def protected_from_env(environ: dict[str, str] | os._Environ[str]) -> tuple[str, ...]:
    raw = environ.get(PROTECTED_ENV, "")
    parts = tuple(p.strip() for p in raw.split(SEPARATOR) if p.strip())
    return parts or DEFAULT_PROTECTED


def basename(program: str) -> str:
    return re.split(r"[\\/]", program)[-1]


# --- shell commands -------------------------------------------------------------------


def tokenise(line: str) -> list[str]:
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    return list(lexer)


def is_punctuation(token: str) -> bool:
    return bool(token) and all(ch in "();<>|&" for ch in token)


def redirect_target(token: str, following: str | None) -> str | None:
    """The file a ``>``-style token writes to, or ``None`` for fd duplication and input."""
    if ">" not in token or following is None:
        return None
    if following.startswith("&") or following.isdigit() or following == "-":
        return None
    return following


def split_segments(tokens: Sequence[str]) -> tuple[list[list[str]], list[str]]:
    """Commands between operators, and every path a redirection writes to."""
    segments: list[list[str]] = []
    targets: list[str] = []
    current: list[str] = []
    skip_next = False
    for index, token in enumerate(tokens):
        if skip_next:
            skip_next = False
            continue
        if not is_punctuation(token):
            current.append(token)
            continue
        following = tokens[index + 1] if index + 1 < len(tokens) else None
        if ">" in token:
            target = redirect_target(token, following)
            if target is not None:
                targets.append(target)
            skip_next = following is not None and not following.startswith("&")
            continue
        if "<" in token:
            skip_next = following is not None
            continue
        if current:
            segments.append(current)
            current = []
    if current:
        segments.append(current)
    return segments, targets


def unwrap(tokens: Sequence[str]) -> tuple[list[str], str | None]:
    """Strip assignments and wrappers; return the program's argv or a nested shell script."""
    rest = list(tokens)
    for _ in range(MAX_DEPTH * 4):
        while rest and (ASSIGNMENT.match(rest[0]) or rest[0] in {"!", "{", "}"}):
            rest.pop(0)
        if not rest:
            return [], None
        program = basename(rest[0])
        if program in SHELLS:
            for index, token in enumerate(rest[1:], start=1):
                if token.startswith("-") and not token.startswith("--") and "c" in token[1:]:
                    script = rest[index + 1] if index + 1 < len(rest) else None
                    return rest, script
            return rest, None
        if program not in WRAPPERS:
            return rest, None
        rest = rest[1:]
        if program == "timeout":
            while rest and rest[0].startswith("-"):
                rest.pop(0)
            if rest:
                rest.pop(0)  # the duration
            continue
        while rest and rest[0].startswith("-"):
            rest.pop(0)
    return rest, None


def git_subcommand(args: Sequence[str]) -> tuple[str | None, list[str]]:
    index = 0
    while index < len(args):
        token = args[index]
        if token in GIT_VALUE_OPTIONS:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        return token, list(args[index + 1 :])
    return None, []


def recursive_force(args: Sequence[str]) -> bool:
    recursive = force = False
    for token in args:
        if token == "--":
            break
        if token.startswith("--"):
            recursive = recursive or token == "--recursive"
            force = force or token == "--force"
        elif token.startswith("-") and len(token) > 1:
            letters = token[1:]
            recursive = recursive or any(ch in letters for ch in "rR")
            force = force or "f" in letters
    return recursive and force


def sed_in_place(args: Sequence[str]) -> bool:
    return any(
        token.startswith(("-i", "--in-place"))
        or (token.startswith("-") and not token.startswith("--") and "i" in token[1:])
        for token in args
    )


def path_arguments(program: str, args: Sequence[str]) -> list[str]:
    paths: list[str] = []
    for token in args:
        if program == "dd":
            if token.startswith("of="):
                paths.append(token[3:])
            continue
        if token.startswith("-") and token != "-":
            continue
        paths.append(token)
    return paths


def protected_write(paths: Sequence[str], protected: Sequence[str], cwd: str) -> str | None:
    for raw in paths:
        rel = normalise(raw, cwd)
        if rel is None:
            continue
        hit = first_protected(rel, protected)
        if hit is not None:
            return f"{rel or '.'} is a protected path ({hit}); it is outside the write boundary"
    return None


def check_program(tokens: Sequence[str], protected: Sequence[str], cwd: str) -> str | None:
    if not tokens:
        return None
    program = basename(tokens[0])
    args = list(tokens[1:])
    if program in NETWORK_PROGRAMS:
        return NETWORK_PROGRAMS[program]
    if program == "git":
        subcommand, sub_args = git_subcommand(args)
        if subcommand in GIT_DENIED:
            return GIT_DENIED[subcommand]
        if subcommand in GIT_TREE_REWRITERS:
            return protected_write(path_arguments("git", sub_args), protected, cwd)
        return None
    if program == "rm" and recursive_force(args):
        return "rm -rf is not allowed; delete named files instead"
    if program == "sed":
        if sed_in_place(args):
            return protected_write(path_arguments(program, args), protected, cwd)
        return None
    if program in MUTATORS:
        return protected_write(path_arguments(program, args), protected, cwd)
    return None


def check_command(command: str, protected: Sequence[str], cwd: str, depth: int = 0) -> str | None:
    if depth > MAX_DEPTH:
        return "the command nests shells too deeply; run it as plain commands"
    for line in command.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            tokens = tokenise(line)
        except ValueError as exc:
            return f"the command could not be parsed ({exc}); split it into plain commands"
        segments, targets = split_segments(tokens)
        reason = protected_write(targets, protected, cwd)
        if reason is not None:
            return reason
        for segment in segments:
            argv, nested = unwrap(segment)
            if nested is not None:
                reason = check_command(nested, protected, cwd, depth + 1)
            else:
                reason = check_program(argv, protected, cwd)
            if reason is not None:
                return reason
    return None


# --- the decision -------------------------------------------------------------------


def decide(payload: dict[str, object], protected: Sequence[str]) -> str | None:
    """The reason to deny this call, or ``None`` to let it through."""
    tool = payload.get("tool_name")
    raw_input = payload.get("tool_input")
    tool_input: dict[str, object] = raw_input if isinstance(raw_input, dict) else {}
    cwd_value = payload.get("cwd")
    cwd = cwd_value if isinstance(cwd_value, str) and cwd_value else os.getcwd()
    if tool == "Bash":
        command = tool_input.get("command")
        if not isinstance(command, str):
            return "the Bash call carries no command"
        return check_command(command, protected, cwd)
    if tool in FILE_TOOLS:
        path = tool_input.get("file_path") or tool_input.get("notebook_path")
        if not isinstance(path, str) or not path:
            return f"the {tool} call carries no file path"
        rel = normalise(path, cwd)
        if rel is None:
            return f"{path} is outside the workspace"
        return protected_write([rel], protected, cwd)
    return None


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "null")
        if not isinstance(payload, dict):
            raise ValueError("the hook input is not a JSON object")
        reason = decide(payload, protected_from_env(os.environ))
    except Exception as exc:  # a hook that cannot decide must deny
        reason = f"the hook could not read its input ({type(exc).__name__}: {exc})"
    if reason is None:
        return 0
    print(f"{PREFIX} {reason}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
