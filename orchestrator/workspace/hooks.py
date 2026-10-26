"""The PreToolUse hook bundle: what each harness loads and how it is checked (TRD §7.3).

Claude Code runs in bare mode, which skips the auto-discovery of
``.claude/settings.json``; the bundle therefore travels as an explicit
``--settings`` document that names the hook script and the permission
deny rules. In a container the bundle directory is mounted read-only at
``/opt/factory/hooks``; on the host it is the factory's ``deploy/hooks``.
Codex has no hooks: its counterpart is the sandbox (``workspace-write``
with network off, ``read-only`` for the reviewer) plus an execpolicy rules
file installed into ``CODEX_HOME``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator import constants
from orchestrator.errors import FactoryError

if TYPE_CHECKING:
    from orchestrator.harness.claude_code import Runner

HOOK_MATCHER = "Bash|Write|Edit|MultiEdit|NotebookEdit"
DENY_RULES: tuple[str, ...] = (
    "Bash(git push)",
    "Bash(git push *)",
    "Bash(git commit)",
    "Bash(git commit *)",
    "Bash(gh)",
    "Bash(gh *)",
    "Bash(curl)",
    "Bash(curl *)",
    "Bash(wget)",
    "Bash(wget *)",
    "Bash(rm -rf *)",
    "Bash(rm -fr *)",
    "WebFetch",
    "WebSearch",
)
CODEX_RULES: tuple[tuple[list[object], str], ...] = (
    (
        ["git", "push"],
        "git push is not allowed; the orchestrator pushes the branch after the gates",
    ),
    (["git", "commit"], "git commit is not allowed; the orchestrator commits with your message"),
    (["gh"], "gh is not available to agents; the orchestrator opens the pull request"),
    (["curl"], "curl is not available to agents; a task reaches only the model proxy"),
    (["wget"], "wget is not available to agents; a task reaches only the model proxy"),
    (
        ["rm", ["-rf", "-fr", "-Rf", "-fR", "-rF", "-Fr"]],
        "rm -rf is not allowed; delete named files",
    ),
    (["rm", "-r", "-f"], "rm -rf is not allowed; delete named files"),
    (["rm", "-f", "-r"], "rm -rf is not allowed; delete named files"),
    (["rm", "--recursive", "--force"], "rm -rf is not allowed; delete named files"),
    (["rm", "--force", "--recursive"], "rm -rf is not allowed; delete named files"),
)
VERIFY_PAYLOAD: dict[str, object] = {
    "hook_event_name": "PreToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "git push origin HEAD"},
}


WRITE_RULES = (
    "import pathlib, sys; p = pathlib.Path(sys.argv[1]); p.parent.mkdir(parents=True,"
    " exist_ok=True); p.write_bytes(sys.stdin.buffer.read())"  # bytes: no code page in the way
)


class HookError(FactoryError):
    """The hook bundle is missing or does not deny; no agent phase may start."""

    code = "hook_error"


def default_hooks_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "deploy" / "hooks"


def hook_script(hooks_dir: str) -> str:
    """The script path as the harness process sees it, with forward slashes."""
    return f"{hooks_dir.replace('\\', '/').rstrip('/')}/{constants.HOOK_SCRIPT}"


def hook_command(hooks_dir: str, python: str) -> str:
    """The hook command line: both paths quoted, forward slashes, so bash and cmd agree."""
    return f'"{python.replace("\\", "/")}" "{hook_script(hooks_dir)}"'


def hook_settings(hooks_dir: str, python: str = "python3") -> dict[str, object]:
    """The ``--settings`` document: the PreToolUse hook and the permission deny rules."""
    return {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": HOOK_MATCHER,
                    "hooks": [
                        {
                            "type": "command",
                            "command": hook_command(hooks_dir, python),
                            "timeout": constants.HOOK_TIMEOUT_S,
                        }
                    ],
                }
            ]
        },
        "permissions": {"deny": list(DENY_RULES)},
    }


def settings_json(hooks_dir: str, python: str = "python3", *, compact: bool = False) -> str:
    document = hook_settings(hooks_dir, python)
    if compact:
        return json.dumps(document, sort_keys=True, separators=(",", ":"))
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def protected_env(protected: Sequence[str]) -> str:
    return constants.PROTECTED_PATHS_SEPARATOR.join(protected)


def agent_env_with_hooks(env: Mapping[str, str], protected: Sequence[str]) -> dict[str, str]:
    """The agent environment plus the protected set the hook enforces."""
    return {**env, constants.PROTECTED_PATHS_ENV: protected_env(protected)}


def codex_rules() -> str:
    """The execpolicy rules file: the Codex counterpart of the hook's command deny list."""
    lines = [
        "# The factory's execpolicy rules (TRD §7.3): the Codex counterpart of the PreToolUse",
        "# hook. Installed by the orchestrator into $CODEX_HOME/rules/factory.rules; the sandbox",
        "# (workspace-write with network off; read-only for the reviewer) and the post-run diff",
        "# remain the enforcement for what a prefix rule cannot see.",
        "",
    ]
    for pattern, justification in CODEX_RULES:
        lines += [
            "prefix_rule(",
            f"    pattern = {json.dumps(pattern)},",
            '    decision = "forbidden",',
            f"    justification = {json.dumps(justification)},",
            ")",
        ]
    return "\n".join(lines) + "\n"


def install_codex_rules(codex_home: Path) -> Path:
    """Write the rules under ``codex_home`` (idempotent); the reviewer's CODEX_HOME on the host."""
    target = codex_home / "rules" / constants.CODEX_RULES_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    text = codex_rules()
    if not target.is_file() or target.read_text(encoding="utf-8") != text:
        target.write_text(text, encoding="utf-8", newline="\n")
    return target


async def verify_hook(
    runner: Runner,
    *,
    hooks_dir: str,
    python: str,
    cwd: Path,
    env: Mapping[str, str] | None = None,
) -> None:
    """Run the hook once with a ``git push`` and require the denial; fail closed otherwise."""
    script = hook_script(hooks_dir)
    outcome = await runner(
        [python, script],
        cwd=cwd,
        env=dict(env or {}),
        timeout_s=constants.HOOK_TIMEOUT_S * 2,
        stdin_text=json.dumps({**VERIFY_PAYLOAD, "cwd": str(cwd)}),
    )
    if outcome.exit_code != 2 or constants.HOOK_DENIAL_PREFIX not in outcome.stderr_tail:
        tail = outcome.stderr_tail[-200:].strip()
        raise HookError(
            f"the PreToolUse hook at {script} did not deny a git push "
            f"(exit {outcome.exit_code}): {tail or 'no output'}"
        )


async def install_codex_rules_in(runner: Runner, *, cwd: Path, python: str = "python3") -> str:
    """Write the execpolicy rules into the container's ``CODEX_HOME`` through its runner."""
    target = f"{constants.CONTAINER_CODEX_HOME}/rules/{constants.CODEX_RULES_FILE}"
    outcome = await runner(
        [python, "-c", WRITE_RULES, target],
        cwd=cwd,
        env={},
        timeout_s=constants.HOOK_TIMEOUT_S * 2,
        stdin_text=codex_rules(),
    )
    if outcome.exit_code != 0:
        raise HookError(
            f"the Codex rules could not be written to {target} (exit {outcome.exit_code}):"
            f" {outcome.stderr_tail[-200:].strip() or 'no output'}"
        )
    return target
