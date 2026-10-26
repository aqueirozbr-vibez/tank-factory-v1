"""Unit 38: the PreToolUse hook, the settings document, the Codex rules and the replay."""

import dataclasses
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
from hypothesis import given
from hypothesis import strategies as st

from orchestrator import constants
from orchestrator.gates.permissions import matches
from orchestrator.harness.base import ToolCall
from orchestrator.harness.claude_code import ClaudeCodeHarness, StreamParser
from orchestrator.harness.codex import CodexHarness
from orchestrator.harness.fake import FakeEffect, FakeScript, FakeToolCall
from orchestrator.harness.process import run_with_timeout
from orchestrator.issue_to_pr import run_build_slice
from orchestrator.trace import TraceWriter
from orchestrator.workspace.container import TaskContainer, exec_env, mounts_for
from orchestrator.workspace.hooks import (
    DENY_RULES,
    HookError,
    agent_env_with_hooks,
    codex_rules,
    default_hooks_dir,
    install_codex_rules,
    settings_json,
    verify_hook,
)
from tests.unit.test_build_phase import (
    ENVELOPE,
    SPEC_REL,
    SRC,
    TASK_ID,
    TEST,
    USAGE,
    deps_for,
    worktree_for,
)
from tests.unit.test_claude_code import _request as claude_request
from tests.unit.test_codex import SCHEMA, VERSION
from tests.unit.test_codex import _request as codex_request

HOOKS = default_hooks_dir()
SCRIPT = HOOKS / constants.HOOK_SCRIPT
CWD = "/work"


def load_hook() -> ModuleType:
    spec = importlib.util.spec_from_file_location("factory_pretooluse", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hook = load_hook()
PROTECTED: tuple[str, ...] = tuple(hook.DEFAULT_PROTECTED)


def bash(command: str, cwd: str = CWD) -> str | None:
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd}
    reason: str | None = hook.decide(payload, PROTECTED)
    return reason


def file_tool(tool: str, path: str, cwd: str = CWD) -> str | None:
    key = "notebook_path" if tool == "NotebookEdit" else "file_path"
    payload = {"tool_name": tool, "tool_input": {key: path}, "cwd": cwd}
    reason: str | None = hook.decide(payload, PROTECTED)
    return reason


# --- the hook's decisions ---------------------------------------------------------------

DENIED_COMMANDS = [
    ("git push origin HEAD", "git push"),
    ("git push", "git push"),
    ("git -C . push --force", "git push"),
    ("git --no-pager -c user.name=x push", "git push"),
    ("git commit -m x", "git commit"),
    ("cd src && git push", "git push"),
    ("ls; git push", "git push"),
    ("echo a | gh pr create", "gh"),
    ("sudo gh auth status", "gh"),
    ("env FOO=1 curl https://example.com", "curl"),
    ("FOO=1 wget https://example.com", "wget"),
    ("/usr/bin/wget -q x", "wget"),
    ("bash -lc 'git push origin main'", "git push"),
    ('sh -c "cd /work && git push"', "git push"),
    ("xargs rm -rf < list.txt", "rm -rf"),
    ("rm -rf build/", "rm -rf"),
    ("rm -fr build", "rm -rf"),
    ("rm -r -f build", "rm -rf"),
    ("rm --recursive --force build", "rm -rf"),
    ("timeout 30 curl x", "curl"),
    ("nohup wget x &", "wget"),
    ("echo x > .github/workflows/ci.yml", "protected path"),
    ("cat body >> CODEOWNERS", "protected path"),
    ("echo x | tee .factory/roster.yaml", "protected path"),
    ("cp a tests/acceptance/test_x.py", "protected path"),
    ("mv a .claude/settings.json", "protected path"),
    ("sed -i s/a/b/ uv.lock", "protected path"),
    ("touch /work/.factory/new.yaml", "protected path"),
    ("git checkout -- .claude/settings.json", "protected path"),
    ("git restore tests/acceptance/", "protected path"),
    ("dd if=/dev/zero of=.factory/x", "protected path"),
    ("cat > .factory/quality.yaml <<EOF\nformat: x\nEOF", "protected path"),
    ("uv run pytest\ngit push", "git push"),
    ("echo 'unterminated", "could not be parsed"),
]
ALLOWED_COMMANDS = [
    "uv run pytest tests/unit -q",
    "git status",
    "git diff --stat",
    "git log --oneline -5",
    "git commit-tree HEAD^{tree}",
    "rm build/tmp.txt",
    "rm -r build",
    "rm -f notes.txt",
    "cat .factory/roster.yaml",
    "ls tests/acceptance",
    "grep -rn push src/",
    "echo hi > notes.txt",
    "echo done 2>&1",
    "make lint >/dev/null 2>&1",
    "python -c 'print(1)'",
    "sed s/a/b/ uv.lock",
    "echo 'git push is denied here'",
    "mkdir -p src/new && touch src/new/__init__.py",
    "cat > docs/notes.md <<EOF\nhello\nEOF",
    "./scripts/gh-report.sh",
    "curl_runner --help",
    "",
    "# just a comment",
]


@pytest.mark.parametrize(("command", "expected"), DENIED_COMMANDS)
def test_denied_commands(command: str, expected: str) -> None:
    reason = bash(command)
    assert reason is not None and expected in reason, (command, reason)


@pytest.mark.parametrize("command", ALLOWED_COMMANDS)
def test_allowed_commands(command: str) -> None:
    assert bash(command) is None, command


def test_file_tools() -> None:
    assert file_tool("Write", "/work/tests/acceptance/test_h.py") is not None
    assert file_tool("Edit", "/work/.factory/roster.yaml") is not None
    assert file_tool("MultiEdit", "/work/.github/workflows/ci.yml") is not None
    assert file_tool("Edit", "/work/src/uv.lock") is not None  # lockfiles by basename anywhere
    assert file_tool("NotebookEdit", "/work/notebooks/a.ipynb") is None
    assert file_tool("Write", "/work/src/x.py") is None
    assert file_tool("Write", "src/x.py") is None
    outside = file_tool("Write", "/tmp/x.py")
    assert outside is not None and "outside the workspace" in outside
    assert file_tool("Write", "/work/../etc/passwd") is not None
    assert file_tool("Edit", "C:\\w\\uv.lock", cwd="C:\\w") is not None
    assert file_tool("Edit", "C:/w/src/a.py", cwd="C:\\w") is None
    assert file_tool("Edit", "c:/W/tests/acceptance/t.py", cwd="C:\\w") is not None
    missing: str | None = hook.decide({"tool_name": "Write", "tool_input": {}}, PROTECTED)
    assert missing is not None and "no file path" in missing
    other: str | None = hook.decide({"tool_name": "Read", "tool_input": {}}, PROTECTED)
    assert other is None


def test_protected_set_comes_from_the_environment() -> None:
    assert hook.protected_from_env({}) == PROTECTED
    assert hook.protected_from_env({"FACTORY_PROTECTED_PATHS": "docs/adr/; b ;"}) == (
        "docs/adr/",
        "b",
    )
    custom = ("docs/adr/",)
    payload = {"tool_name": "Write", "tool_input": {"file_path": "/work/docs/adr/0001.md"}}
    denied: str | None = hook.decide({**payload, "cwd": CWD}, custom)
    assert denied is not None
    payload = {"tool_name": "Write", "tool_input": {"file_path": "/work/.factory/x"}}
    allowed: str | None = hook.decide({**payload, "cwd": CWD}, custom)
    assert allowed is None  # the environment replaces the default set
    env = agent_env_with_hooks({"A": "1"}, ("x/", "y"))
    assert env == {"A": "1", "FACTORY_PROTECTED_PATHS": "x/;y"}


@given(
    path=st.from_regex(r"[a-z.]{1,6}(/[a-z.]{1,6}){0,3}", fullmatch=True),
    pattern=st.sampled_from([*PROTECTED, "**", "src/", "x.py"]),
)
def test_matcher_agrees_with_the_gate(path: str, pattern: str) -> None:
    assert hook.protected_match(path, pattern) == matches(path, pattern)


def run_hook(stdin: str, **extra_env: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **extra_env}
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )


def test_script_exit_codes_and_reasons() -> None:
    assert hook.PREFIX == constants.HOOK_DENIAL_PREFIX
    assert hook.PROTECTED_ENV == constants.PROTECTED_PATHS_ENV
    push = {"tool_name": "Bash", "tool_input": {"command": "git push origin main"}, "cwd": CWD}
    denied = run_hook(json.dumps(push))
    assert denied.returncode == 2 and denied.stdout == ""
    assert denied.stderr.startswith(constants.HOOK_DENIAL_PREFIX)
    assert "git push is not allowed" in denied.stderr
    ok = run_hook(json.dumps({**push, "tool_input": {"command": "git status"}}))
    assert ok.returncode == 0 and ok.stdout == "" and ok.stderr == ""
    garbage = run_hook("not json")
    assert garbage.returncode == 2 and "could not read its input" in garbage.stderr
    empty = run_hook("")
    assert empty.returncode == 2
    adr = {"tool_name": "Write", "tool_input": {"file_path": "/work/docs/adr/1.md"}, "cwd": CWD}
    assert run_hook(json.dumps(adr), FACTORY_PROTECTED_PATHS="docs/adr/").returncode == 2
    assert run_hook(json.dumps(adr)).returncode == 0


# --- what the harnesses load ------------------------------------------------------------


def test_settings_document_and_shipped_fragment() -> None:
    parsed = json.loads(settings_json(constants.HOOKS_MOUNT))
    entry = parsed["hooks"]["PreToolUse"][0]
    assert entry["matcher"] == "Bash|Write|Edit|MultiEdit|NotebookEdit"
    assert entry["hooks"] == [
        {
            "type": "command",
            "command": '"python3" "/opt/factory/hooks/factory_pretooluse.py"',
            "timeout": constants.HOOK_TIMEOUT_S,
        }
    ]
    assert parsed["permissions"]["deny"] == list(DENY_RULES)
    assert {
        "Bash(git push *)",
        "Bash(gh *)",
        "Bash(curl *)",
        "Bash(wget *)",
        "Bash(rm -rf *)",
    } <= set(DENY_RULES)
    shipped = (HOOKS / "settings.json").read_text(encoding="utf-8")
    assert shipped == settings_json(constants.HOOKS_MOUNT)
    host = json.loads(settings_json("C:\\f\\deploy\\hooks", "C:\\py\\python.exe"))
    command = host["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert command == '"C:/py/python.exe" "C:/f/deploy/hooks/factory_pretooluse.py"'


def test_claude_code_argv_carries_the_settings(tmp_path: Path) -> None:
    harness = ClaudeCodeHarness("claude", version_probe=lambda: "2.1.309 (Claude Code)")
    plain = harness.build_argv(claude_request(tmp_path))
    assert "--settings" not in plain
    hooked = harness.build_argv(
        dataclasses.replace(claude_request(tmp_path), hooks_dir=constants.HOOKS_MOUNT)
    )
    assert hooked[hooked.index("--settings") + 1] == settings_json(
        constants.HOOKS_MOUNT, compact=True
    )
    assert hooked.index("--settings") < hooked.index("--system-prompt")


def test_stream_parser_marks_hook_denials() -> None:
    calls: list[ToolCall] = []
    parser = StreamParser(calls.append)
    use = {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "git push"}},
                {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "ls"}},
            ]
        },
    }
    results = {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "is_error": True,
                    "content": "PreToolUse:Bash hook error: [python3 hook]: factory-hook: denied: "
                    "git push is not allowed; the orchestrator pushes the branch",
                },
                {"type": "tool_result", "tool_use_id": "t2", "is_error": True, "content": "boom"},
            ]
        },
    }
    parser.feed(json.dumps(use))
    parser.feed(json.dumps(results))
    assert [(c.tool_call_id, c.ok, c.denied) for c in calls] == [
        ("t1", False, True),
        ("t2", False, False),
    ]


def test_codex_rules_and_sandbox_flags(tmp_path: Path) -> None:
    text = codex_rules()
    assert text == (HOOKS / "factory.rules").read_text(encoding="utf-8")
    assert text.count('decision = "forbidden"') == 10
    assert 'pattern = ["git", "push"],' in text and 'pattern = ["gh"],' in text
    assert 'pattern = ["rm", ["-rf", "-fr", "-Rf", "-fR", "-rF", "-Fr"]],' in text
    installed = install_codex_rules(tmp_path / "codex-home")
    assert installed == tmp_path / "codex-home" / "rules" / "factory.rules"
    assert installed.read_text(encoding="utf-8") == text
    assert install_codex_rules(tmp_path / "codex-home") == installed
    harness = CodexHarness("codex", version_probe=lambda: VERSION)
    argv = harness.build_argv(codex_request(tmp_path), schema_path=SCHEMA)
    flag = argv.index("sandbox_workspace_write.network_access=false")
    assert argv[flag - 1] == "-c"


async def test_verify_hook_passes_on_the_script_and_fails_without_it(tmp_path: Path) -> None:
    await verify_hook(run_with_timeout, hooks_dir=str(HOOKS), python=sys.executable, cwd=tmp_path)
    with pytest.raises(HookError, match="did not deny"):
        await verify_hook(
            run_with_timeout, hooks_dir=str(tmp_path / "none"), python=sys.executable, cwd=tmp_path
        )


def test_container_mount_and_codex_home(product: Path, tmp_path: Path) -> None:
    wt = worktree_for(product, tmp_path)
    mounts = mounts_for(wt, hooks_dir=Path("/srv/factory/deploy/hooks"))
    bundle = mounts[-1]
    assert Path(bundle.host) == Path("/srv/factory/deploy/hooks")  # host form of the host
    assert (bundle.container, bundle.mode) == ("/opt/factory/hooks", "ro")
    assert len(mounts_for(wt)) == len(mounts) - 1
    container = TaskContainer("factory-task-418-1", "img", wt.path, wt.path.name)
    inside = exec_env(container, {"CODEX_HOME": "C:/data/codex-home", "A": "1"})
    assert inside["CODEX_HOME"] == "/home/agent/.codex" and inside["A"] == "1"


# --- the fake-harness replay --------------------------------------------------------------

DENIED_CALLS = (
    FakeToolCall(tool="Bash", args={"command": "git push origin HEAD"}),
    FakeToolCall(tool="Bash", args={"command": "gh pr create --fill"}),
    FakeToolCall(tool="Bash", args={"command": "rm -rf tests"}),
    FakeToolCall(tool="Bash", args={"command": "curl https://pypi.org/simple/"}),
    FakeToolCall(tool="Bash", args={"command": "wget https://github.com"}),
    FakeToolCall(tool="Write", args={"file_path": "tests/acceptance/test_h.py", "content": "x"}),
    FakeToolCall(
        tool="Edit",
        args={"file_path": ".factory/roster.yaml", "old_string": "a", "new_string": "b"},
    ),
)
ALLOWED_CALLS = (
    FakeToolCall(tool="Edit", args={"file_path": "src/billing/invoice.py"}),
    FakeToolCall(tool="Bash", args={"command": "uv run pytest tests/unit -q"}),
    FakeToolCall(tool="Read", args={"file_path": "README.md"}),
)
REPLAY = FakeScript(
    session_id="sess-hooks",
    tool_calls=(*DENIED_CALLS, *ALLOWED_CALLS),
    effects=(
        FakeEffect(write="src/billing/invoice.py", content=SRC),
        FakeEffect(write="tests/unit/test_invoice_due_date.py", content=TEST),
    ),
    envelope=ENVELOPE,
    usage=USAGE,
)


async def test_build_replay_records_hook_denials(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    deps, harness, _ = deps_for(trace_writer, [REPLAY])
    deps = dataclasses.replace(deps, hooks_dir=str(HOOKS), hook_python=sys.executable)
    wt = worktree_for(product, tmp_path)
    outcome = await run_build_slice(deps, task_id=TASK_ID, worktree=wt, spec_rel=SPEC_REL)
    assert outcome.passed and outcome.repairs == 0
    request = harness.calls[0]
    assert request.hooks_dir == str(HOOKS)
    assert request.env["FACTORY_PROTECTED_PATHS"].startswith(".claude/;")
    rows = trace_writer.db.fetchall(
        "SELECT payload_json FROM events WHERE kind = 'tool_call' ORDER BY seq"
    )
    payloads = [json.loads(r["payload_json"]) for r in rows]
    assert len(payloads) == len(DENIED_CALLS) + len(ALLOWED_CALLS)
    denied = [p for p in payloads if p["denied"]]
    assert len(denied) == len(DENIED_CALLS)
    assert all(p["ok"] is False for p in denied)
    assert all(p["result_snippet"].startswith("PreToolUse:") for p in denied)
    assert "git push is not allowed" in denied[0]["result_snippet"]
    assert "protected path" in denied[5]["result_snippet"]
    assert all(p["ok"] is True and not p["denied"] for p in payloads if not p["denied"])
    agent_end = trace_writer.db.fetchone("SELECT payload_json FROM events WHERE kind = 'agent_end'")
    assert agent_end is not None
    assert json.loads(agent_end["payload_json"])["hook_denials"] == len(DENIED_CALLS)
