"""``factory authz``: generate ``authz.py`` and ``authz.ts`` from ``authz.yaml`` (TRD §6.2)."""

import json
from pathlib import Path

from orchestrator.authz import (
    CASES_JSON,
    GENERATED_PY,
    GENERATED_TS,
    TABLE_PATH,
    Table,
    case_rows,
    load_table,
)

HEADER = "Generated from authz.yaml by `factory authz`; never hand-edited (TRD §6.2)."


def _block(name: str, annotation: str, values: list[str]) -> list[str]:
    """``NAME: annotation = frozenset({...})`` exploded one item per line, as ruff formats it."""
    return [
        f"{name}: {annotation} = frozenset(",
        "    {",
        *[f'        "{value}",' for value in values],
        "    }",
        ")",
    ]


def render_python(table: Table) -> str:
    rules = sorted(table.rules)
    lines = [
        f'"""{HEADER}"""',
        "",
        f'TABLE_SHA256 = "{table.sha256}"',
        *_block("PERMISSIONS", "frozenset[str]", sorted(table.permissions)),
        *_block("ACTIONS", "frozenset[str]", sorted(table.actions)),
        *_block("CONDITIONS", "frozenset[str]", sorted(table.conditions)),
        "RULES: frozenset[tuple[str, str, str]] = frozenset(",
        "    {",
        *[f'        ("{p}", "{a}", "{c}"),' for p, a, c in rules],
        "    }",
        ")",
        "",
        "",
        "def allowed(permission: str, action: str, *, codeowner: bool = False) -> bool:",
        '    """Default deny: a row must name permission, action and a condition that holds."""',
        '    if (permission, action, "always") in RULES:',
        "        return True",
        '    return codeowner and (permission, action, "codeowner") in RULES',
        "",
        "",
        '__all__ = ["ACTIONS", "CONDITIONS", "PERMISSIONS", "RULES", "TABLE_SHA256", "allowed"]',
        "",
    ]
    return "\n".join(lines)


def render_typescript(table: Table) -> str:
    rules = sorted(table.rules)
    rows = ",\n".join(f'  "{p}|{a}|{c}"' for p, a, c in rules)
    permissions = json.dumps(sorted(table.permissions))
    actions = json.dumps(sorted(table.actions))
    conditions = json.dumps(sorted(table.conditions))
    return (
        f"// {HEADER}\n"
        f'export const TABLE_SHA256 = "{table.sha256}";\n'
        f"export const PERMISSIONS: ReadonlySet<string> = new Set({permissions});\n"
        f"export const ACTIONS: ReadonlySet<string> = new Set({actions});\n"
        f"export const CONDITIONS: ReadonlySet<string> = new Set({conditions});\n"
        f"const RULES: ReadonlySet<string> = new Set([\n{rows},\n]);\n"
        "\n"
        "/** Default deny: a row must name permission, action and a condition that holds. */\n"
        "export function allowed(permission: string, action: string, codeowner = false):"
        " boolean {\n"
        "  if (RULES.has(`${permission}|${action}|always`)) return true;\n"
        "  return codeowner && RULES.has(`${permission}|${action}|codeowner`);\n"
        "}\n"
    )


def render_cases(table: Table) -> str:
    return json.dumps(case_rows(table.cases), indent=2) + "\n"


def generate(
    table_path: Path = TABLE_PATH,
    *,
    python_out: Path = GENERATED_PY,
    ts_out: Path = GENERATED_TS,
    cases_out: Path = CASES_JSON,
) -> Table:
    table = load_table(table_path)
    python_out.write_text(render_python(table), encoding="utf-8", newline="\n")
    ts_out.parent.mkdir(parents=True, exist_ok=True)
    ts_out.write_text(render_typescript(table), encoding="utf-8", newline="\n")
    cases_out.write_text(render_cases(table), encoding="utf-8", newline="\n")
    return table


def check(
    table_path: Path = TABLE_PATH,
    *,
    python_out: Path = GENERATED_PY,
    ts_out: Path = GENERATED_TS,
    cases_out: Path = CASES_JSON,
) -> list[str]:
    """The paths whose committed content differs from what the table generates."""
    table = load_table(table_path)
    expected = {
        python_out: render_python(table),
        ts_out: render_typescript(table),
        cases_out: render_cases(table),
    }
    stale: list[str] = []
    for path, text in expected.items():
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            stale.append(str(path))
    return stale


__all__ = ["check", "generate", "render_cases", "render_python", "render_typescript"]
