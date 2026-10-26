"""``factory dev seed``: a scratch product repository for the phase 1 gate.

Ten hand-written specs over a tiny ``demo`` package, each with a fake-harness
script whose effects satisfy the real format, lint, typecheck and test
commands (PRD §Scope · Phase 1 gate).
"""

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from orchestrator.phases.plan import spec_path_for
from orchestrator.workspace.git import run_git

FACTORY_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class SeedTask:
    issue: int
    name: str
    signature: str
    body: str
    docstring: str
    outcome: str
    test_call: str
    test_expected: str


TASKS: tuple[SeedTask, ...] = (
    SeedTask(
        1,
        "slugify",
        "slugify(text: str) -> str",
        'return "-".join(text.lower().split())',
        "Lower-case words joined by hyphens.",
        "a URL-safe slug for a title",
        'slugify("Hello World")',
        '"hello-world"',
    ),
    SeedTask(
        2,
        "clamp",
        "clamp(value: float, low: float, high: float) -> float",
        "return max(low, min(high, value))",
        "Clamp a value into an inclusive range.",
        "a value bounded to a range",
        "clamp(15, 0, 10)",
        "10",
    ),
    SeedTask(
        3,
        "chunk",
        "chunk(items: list[int], size: int) -> list[list[int]]",
        "return [items[i : i + size] for i in range(0, len(items), size)]",
        "Split a list into consecutive chunks.",
        "a list split into fixed-size pieces",
        "chunk([1, 2, 3, 4, 5], 2)",
        "[[1, 2], [3, 4], [5]]",
    ),
    SeedTask(
        4,
        "is_palindrome",
        "is_palindrome(text: str) -> bool",
        'cleaned = "".join(c for c in text.lower() if c.isalnum())\n'
        "    return cleaned == cleaned[::-1]",
        "True when the letters read the same both ways.",
        "a palindrome check that ignores case and punctuation",
        'is_palindrome("Racecar")',
        "True",
    ),
    SeedTask(
        5,
        "mean",
        "mean(values: list[float]) -> float",
        "if not values:\n"
        '        raise ValueError("mean of an empty list")\n'
        "    return sum(values) / len(values)",
        "Arithmetic mean; empty input is an error.",
        "the mean of a list of numbers",
        "mean([1, 2, 3])",
        "2",
    ),
    SeedTask(
        6,
        "dedupe",
        "dedupe(items: list[str]) -> list[str]",
        "return list(dict.fromkeys(items))",
        "Unique items in first-seen order.",
        "a list without duplicates, order kept",
        'dedupe(["a", "b", "a"])',
        '["a", "b"]',
    ),
    SeedTask(
        7,
        "title_case",
        "title_case(text: str) -> str",
        'return " ".join(w[:1].upper() + w[1:].lower() for w in text.split())',
        "Capitalise each word.",
        "a title-cased string",
        'title_case("hello wORLD")',
        '"Hello World"',
    ),
    SeedTask(
        8,
        "percent",
        "percent(part: float, whole: float) -> float",
        "if whole == 0:\n        return 0.0\n    return round(part / whole * 100, 2)",
        "Percentage of part in whole, two decimals.",
        "a percentage that is safe for a zero whole",
        "percent(1, 4)",
        "25.0",
    ),
    SeedTask(
        9,
        "flatten",
        "flatten(nested: list[list[int]]) -> list[int]",
        "return [item for row in nested for item in row]",
        "One level of flattening.",
        "a flat list from a list of lists",
        "flatten([[1, 2], [3]])",
        "[1, 2, 3]",
    ),
    SeedTask(
        10,
        "count_words",
        "count_words(text: str) -> dict[str, int]",
        "counts: dict[str, int] = {}\n"
        "    for word in text.lower().split():\n"
        "        counts[word] = counts.get(word, 0) + 1\n"
        "    return counts",
        "Case-insensitive word counts.",
        "word counts for a text",
        'count_words("a b A")',
        '{"a": 2, "b": 1}',
    ),
)

PYPROJECT = """[project]
name = "demo"
version = "0.1.0"
requires-python = ">=3.12"

[tool.ruff]
line-length = 100
target-version = "py312"
extend-exclude = [".factory-dev"]

[tool.mypy]
python_version = "3.12"
strict = true
files = ["demo", "tests"]
exclude = ["(^|/).factory-dev/"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
"""


AMBIGUOUS = SeedTask(
    11,
    "ratio",
    "ratio(part: float, whole: float) -> int",
    "return round(100 * part / whole)",
    "Share of a whole as a rounded percentage.",
    "a rounded share of a whole",
    "ratio(1, 3)",
    "33",
)
AMBIGUOUS_QUESTION = "Should ratio round half up or half to even?"
ALL_TASKS: tuple[SeedTask, ...] = (*TASKS, AMBIGUOUS)


def seed_index(task: SeedTask) -> int:
    """The task's place in the seed (1 to 11), whatever issue number it replays under."""
    for position, seeded in enumerate(ALL_TASKS, start=1):
        if seeded.name == task.name:
            return position
    return task.issue


def spec_rel_for(task: SeedTask) -> str:
    """The path the planner chooses for this issue title (specs/<n>-<slug>.md)."""
    return spec_path_for(task.issue, task.name)


def issue_body(task: SeedTask) -> str:
    """The issue as the template renders it (GitHub issue forms use h3 headings)."""
    return (
        f"### Outcome\n\nThe demo package offers {task.outcome}.\n\n"
        f"### Scope\n\n- demo/{task.name}.py\n- tests/unit/test_{task.name}.py\n\n"
        "### Non-goals\n\n- Any change outside the two files in Scope.\n\n"
        "### Acceptance criteria\n\n"
        f"1. WHEN `{task.test_call}` is evaluated THE SYSTEM SHALL return"
        f" `{task.test_expected}`.\n\n"
        "### Holdout\n\n- tests/acceptance/test_holdout.py::test_holdout\n"
    )


def module_source(task: SeedTask) -> str:
    return (
        f'"""{task.name}: {task.docstring}"""\n\n\n'
        f"def {task.signature}:\n"
        f'    """{task.docstring}"""\n'
        f"    {task.body}\n"
    )


def test_source(task: SeedTask) -> str:
    return (
        f"from demo.{task.name} import {task.name}\n\n\n"
        f"def test_{task.name}() -> None:\n"
        f"    assert {task.test_call} == {task.test_expected}\n"
    )


def spec_source(task: SeedTask) -> str:
    module = f"demo/{task.name}.py"
    test = f"tests/unit/test_{task.name}.py"
    return (
        f"# {task.issue} — {task.name}\n\nrisk: risk:low\n\n"
        f"## Outcome\n\nThe demo package offers {task.outcome}.\n\n"
        f"## Scope\n\n- {module}\n- {test}\n\n"
        "## Non-goals\n\n- Any change outside the two files in Scope.\n\n"
        "## Acceptance criteria\n\n"
        f"1. WHEN `{task.test_call}` is evaluated"
        f" THE SYSTEM SHALL return `{task.test_expected}`.\n\n"
        "## Constraints\n\nNo ADR applies.\n\n"
        + (
            "## Open questions\n\n- " + AMBIGUOUS_QUESTION + "\n\n"
            if task is AMBIGUOUS
            else "## Open questions\n\nNone.\n\n"
        )
        + "## Test plan\n\n- AC1 -> tests/acceptance/test_holdout.py::test_holdout\n"
        f"- AC1 unit -> {test}::test_{task.name}\n"
    )


def script_for(task: SeedTask, *, violation: bool) -> dict[str, object]:
    module = f"demo/{task.name}.py"
    test = f"tests/unit/test_{task.name}.py"
    effects: list[dict[str, str]] = [
        {"write": module, "content": module_source(task)},
        {"write": test, "content": test_source(task)},
    ]
    if violation:
        effects.append(
            {
                "write": "tests/acceptance/test_holdout.py",
                "content": "def test_holdout() -> None:\n    pass\n",
            }
        )
    return {
        "session_id": f"seed-{task.issue:02d}",
        "tool_calls": [
            {
                "tool": "Read",
                "args": {"file_path": "demo/__init__.py"},
                "result_snippet": "1 line",
                "ok": True,
                "duration_ms": 5,
            },
            {
                "tool": "Write",
                "args": {"file_path": module},
                "result_snippet": "written",
                "ok": True,
                "duration_ms": 9,
            },
            {
                "tool": "Write",
                "args": {"file_path": test},
                "result_snippet": "written",
                "ok": True,
                "duration_ms": 8,
            },
            {
                "tool": "Bash",
                "args": {"command": f"pytest -q {test}"},
                "result_snippet": "1 passed",
                "ok": True,
                "duration_ms": 420,
            },
        ],
        "effects": effects,
        "envelope": {
            "status": "success",
            "summary": f"Add {task.name} with a unit test",
            "changed_files": [module, test],
            "tests_added": [test],
            "commit_message": f"feat(demo): {task.name} (#{task.issue})",
            "notes_for_next_agent": task.docstring,
        },
        "usage": {
            "input_tokens": 9000 + task.issue * 100,
            "output_tokens": 800 + task.issue * 10,
            "cache_read_tokens": 6000,
            "cache_write_tokens": 0,
            "cost_usd": round(0.30 + seed_index(task) * 0.01, 2),
        },
    }


def planner_script_for(task: SeedTask) -> dict[str, object]:
    spec_rel = spec_rel_for(task)
    ambiguous = task is AMBIGUOUS
    return {
        "session_id": f"plan-{task.issue:02d}",
        "tool_calls": [
            {"tool": "Read", "args": {"file_path": "demo/__init__.py"}, "ok": True},
            {"tool": "Write", "args": {"file_path": spec_rel}, "ok": True},
        ],
        "effects": [{"write": spec_rel, "content": spec_source(task)}],
        "envelope": {
            "status": "fail" if ambiguous else "success",
            "summary": f"Spec for {task.name}",
            "artifacts": [spec_rel],
            "notes_for_next_agent": task.docstring,
            "risk_inputs": {
                "tiers_touched": ["C"],
                "est_lines": 20,
                "new_dependencies": [],
                "modules": [task.name],
            },
            "open_questions": [AMBIGUOUS_QUESTION] if ambiguous else [],
        },
        "usage": {"input_tokens": 4000, "output_tokens": 600, "cost_usd": 0.12},
    }


def reviewer_script_for(task: SeedTask) -> dict[str, object]:
    module = f"demo/{task.name}.py"
    test = f"tests/unit/test_{task.name}.py"
    return {
        "session_id": f"review-{task.issue:02d}",
        "tool_calls": [
            {"tool": "Read", "args": {"file_path": module}, "ok": True},
            {"tool": "Grep", "args": {"pattern": task.name, "path": test}, "ok": True},
        ],
        "envelope": {
            "approved": True,
            "verdict": "accepted",
            "findings": [
                {
                    "requirement_id": "AC1",
                    "met": True,
                    "severity": "Nit",
                    "file": module,
                    "line": 4,
                    "problem": "Met.",
                    "evidence": f"{test}::test_{task.name}",
                }
            ],
            "blocking": [],
        },
        "usage": {"input_tokens": 6000, "output_tokens": 400, "cost_usd": 0.09},
    }


def _write(root: Path, rel: str, content: str) -> None:
    target = root / Path(*rel.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")


def quality_yaml(factory_root: Path) -> str:
    prefix = f'["uv", "run", "--project", "{factory_root.as_posix()}",'
    return (
        "# Seeded: the tools come from the factory's own environment.\n"
        f'format: {prefix} "ruff", "format", "--check", "."]\n'
        f'lint: {prefix} "ruff", "check", "."]\n'
        f'typecheck: {prefix} "mypy", "."]\n'
        f'test: {prefix} "pytest", "-q"]\n'
        f'full_suite: {prefix} "pytest", "-q"]\n'
    )


def seed_repository(
    target: Path, *, factory_root: Path = FACTORY_ROOT, with_violations: bool = False
) -> list[Path]:
    """Create the scratch repository; return the spec paths in issue order."""
    if target.exists() and any(target.iterdir()):
        raise FileExistsError(f"{target} is not empty")
    target.mkdir(parents=True, exist_ok=True)
    _write(target, "pyproject.toml", PYPROJECT)
    _write(target, "README.md", "# demo\n\nScratch repository seeded by `factory dev seed`.\n")
    _write(target, "demo/__init__.py", '"""The demo package."""\n')
    _write(target, "tests/__init__.py", "")
    _write(target, "tests/unit/__init__.py", "")
    _write(
        target,
        "tests/unit/test_package.py",
        "import demo\n\n\ndef test_package_imports() -> None:\n    assert demo.__doc__\n",
    )
    _write(target, "tests/acceptance/__init__.py", "")
    _write(
        target, "tests/acceptance/test_holdout.py", "def test_holdout() -> None:\n    assert True\n"
    )
    _write(target, "CODEOWNERS", "* @acme/platform\n")
    _write(target, ".github/workflows/ci.yml", "name: ci\non: [push]\njobs: {}\n")
    _write(
        target,
        ".gitignore",
        ".venv/\n__pycache__/\n.pytest_cache/\n.mypy_cache/\n.ruff_cache/\n.factory-dev/data/\n",
    )
    shutil.copytree(factory_root / "fixtures" / "factory_config", target / ".factory")
    shutil.copytree(
        factory_root / "deploy" / "product-repo" / ".github",
        target / ".github",
        dirs_exist_ok=True,
    )
    _write(target, ".factory/quality.yaml", quality_yaml(factory_root))
    shutil.copytree(
        factory_root / "fixtures" / "known_failing", target / ".factory-dev" / "known_failing"
    )
    specs: list[Path] = []
    for task in ALL_TASKS:
        _write(
            target,
            f".factory-dev/scripts/planner/{task.issue}.json",
            json.dumps(planner_script_for(task), indent=2) + "\n",
        )
        _write(
            target,
            f".factory-dev/scripts/reviewer/{task.issue}.json",
            json.dumps(reviewer_script_for(task), indent=2) + "\n",
        )
    for task in TASKS:
        rel = spec_rel_for(task)
        _write(target, rel, spec_source(task))
        specs.append(target / Path(*rel.split("/")))
        _write(
            target,
            f".factory-dev/scripts/{task.issue}.json",
            json.dumps(script_for(task, violation=False), indent=2) + "\n",
        )
        if with_violations:
            _write(
                target,
                f".factory-dev/scripts/{task.issue}-violation.json",
                json.dumps(script_for(task, violation=True), indent=2) + "\n",
            )
    run_git(["init", "-q", "-b", "main"], cwd=target)
    run_git(["config", "user.email", "seed@factory.local"], cwd=target)
    run_git(["config", "user.name", "factory seed"], cwd=target)
    run_git(["config", "core.autocrlf", "false"], cwd=target)
    run_git(["add", "-A"], cwd=target)
    run_git(
        ["commit", "-q", "-m", "seed: demo package, ten specs, factory configuration"], cwd=target
    )
    return specs
