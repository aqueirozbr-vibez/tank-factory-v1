"""Authorisation: the decision table, its generated module and the permission lookup (TRD §6.2).

``authz.yaml`` at the repository root is the policy; ``authz.py`` beside this
file is generated from it and never hand-edited. ``decide`` answers from the
generated table with default deny; ``verify_generated`` refuses start-up when
the committed module no longer matches the table or the table does not
parse; ``resolve_permission`` is the GitHub collaborator permission cached
for five minutes, ``None`` when GitHub is unreachable, which every write
treats as a deny.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml

from orchestrator import constants
from orchestrator.authz import authz as generated
from orchestrator.errors import ConfigError
from orchestrator.github.client import GitHubApiError
from orchestrator.intake.ports import GitHubPort
from orchestrator.trace.writer import TraceWriter

TABLE_PATH = Path(__file__).resolve().parents[2] / "authz.yaml"
GENERATED_PY = Path(__file__).resolve().parent / "authz.py"
GENERATED_TS = Path(__file__).resolve().parents[2] / "console" / "lib" / "authz.ts"
CASES_JSON = Path(__file__).resolve().parents[2] / "console" / "lib" / "authz.cases.json"


@dataclass(frozen=True)
class Case:
    permission: str
    action: str
    codeowner: bool
    allow: bool


@dataclass(frozen=True)
class Table:
    version: int
    permissions: tuple[str, ...]
    actions: dict[str, str]
    conditions: dict[str, str]
    rules: frozenset[tuple[str, str, str]]
    cases: tuple[Case, ...]
    sha256: str


def _names(value: object, *, field: str) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return list(value)
    raise ConfigError(f"authz.yaml: {field} must be a name or a list of names")


def load_table(path: Path = TABLE_PATH) -> Table:
    """Parse and validate the table; any defect is a ``ConfigError`` (start-up aborts)."""
    try:
        raw_bytes = path.read_bytes()
        data = yaml.safe_load(raw_bytes)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"authz.yaml cannot be read: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("authz.yaml must be a mapping")
    version = data.get("version")
    if version != 1:
        raise ConfigError(f"authz.yaml version {version!r} is not 1")
    permissions = tuple(_names(data.get("permissions"), field="permissions"))
    actions = data.get("actions")
    conditions = data.get("conditions")
    if not isinstance(actions, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in actions.items()
    ):
        raise ConfigError("authz.yaml: actions must map names to descriptions")
    if not isinstance(conditions, dict) or "always" not in conditions:
        raise ConfigError(
            "authz.yaml: conditions must map names to descriptions and include always"
        )
    rules: set[tuple[str, str, str]] = set()
    raw_rules = data.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise ConfigError("authz.yaml: rules must be a non-empty list")
    for index, rule in enumerate(raw_rules):
        if not isinstance(rule, dict):
            raise ConfigError(f"authz.yaml: rule {index} is not a mapping")
        condition = rule.get("condition", "always")
        if condition not in conditions:
            raise ConfigError(f"authz.yaml: rule {index} names unknown condition {condition!r}")
        for permission in _names(rule.get("permission"), field=f"rule {index} permission"):
            if permission not in permissions:
                raise ConfigError(
                    f"authz.yaml: rule {index} names unknown permission {permission!r}"
                )
            for action in _names(rule.get("action"), field=f"rule {index} action"):
                if action not in actions:
                    raise ConfigError(f"authz.yaml: rule {index} names unknown action {action!r}")
                rules.add((permission, action, str(condition)))
    cases: list[Case] = []
    for index, case in enumerate(data.get("cases") or []):
        if not isinstance(case, dict) or not isinstance(case.get("allow"), bool):
            raise ConfigError(
                f"authz.yaml: case {index} needs permission, action, codeowner, allow"
            )
        cases.append(
            Case(
                str(case.get("permission", "")),
                str(case.get("action", "")),
                bool(case.get("codeowner", False)),
                bool(case["allow"]),
            )
        )
    return Table(
        version=1,
        permissions=permissions,
        actions=dict(actions),
        conditions=dict(conditions),
        rules=frozenset(rules),
        cases=tuple(cases),
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
    )


def evaluate(
    table_rules: frozenset[tuple[str, str, str]], permission: str, action: str, *, codeowner: bool
) -> bool:
    """The one rule of evaluation, shared by the generated modules: default deny."""
    if (permission, action, "always") in table_rules:
        return True
    return codeowner and (permission, action, "codeowner") in table_rules


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    permission: str | None
    action: str


def decide(permission: str | None, action: str, *, codeowner: bool = False) -> Decision:
    """Answer from the generated table; an unknown permission (GitHub unreachable) denies."""
    if permission is None:
        return Decision(
            False, "permission unknown: GitHub unreachable and no fresh cache", None, action
        )
    if action not in generated.ACTIONS:
        return Decision(False, f"unknown action {action!r}", permission, action)
    if permission not in generated.PERMISSIONS:
        return Decision(False, f"permission {permission!r} grants nothing", permission, action)
    if generated.allowed(permission, action, codeowner=codeowner):
        return Decision(True, "allowed by authz.yaml", permission, action)
    why = (
        "needs a CODEOWNER of the governed path"
        if action == "governed_approve"
        else "denied by authz.yaml"
    )
    return Decision(False, why, permission, action)


def verify_generated(path: Path = TABLE_PATH) -> Table:
    """Start-up check: the table parses and the committed module was generated from it."""
    table = load_table(path)
    if table.sha256 != generated.TABLE_SHA256:
        raise ConfigError(
            "orchestrator/authz/authz.py was not generated from authz.yaml: run `factory authz`"
        )
    if table.rules != generated.RULES:
        raise ConfigError(
            "orchestrator/authz/authz.py rules differ from authz.yaml: run `factory authz`"
        )
    return table


async def resolve_permission(
    writer: TraceWriter, github: GitHubPort, login: str | None, *, task_id: str
) -> str | None:
    """The collaborator permission, cached five minutes; ``None`` when GitHub cannot answer."""
    if not login or login.endswith("[bot]"):
        return "none"
    cached = writer.cached_permission(login, ttl_s=constants.PERMISSION_CACHE_S)
    if cached is not None:
        return cached
    try:
        permission = await github.collaborator_permission(login, task_id=task_id)
    except (GitHubApiError, httpx.HTTPError, OSError):
        return None
    writer.cache_permission(login, permission)
    return permission


def case_rows(cases: Sequence[Case]) -> list[dict[str, object]]:
    return [
        {"permission": c.permission, "action": c.action, "codeowner": c.codeowner, "allow": c.allow}
        for c in cases
    ]


__all__ = [
    "CASES_JSON",
    "GENERATED_PY",
    "GENERATED_TS",
    "TABLE_PATH",
    "Case",
    "Decision",
    "Table",
    "case_rows",
    "decide",
    "evaluate",
    "load_table",
    "resolve_permission",
    "verify_generated",
]
