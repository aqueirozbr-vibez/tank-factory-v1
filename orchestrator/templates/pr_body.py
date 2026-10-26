"""The PR body: trace rows in, Markdown out; no agent writes it (PRD §Capability 5; TRD §8).

Every field comes from ``tasks``, ``phases``, ``gate_results``, the run's
``verdict_policy`` event and the spec file. Agent-authored text appears
only in the named places (finding problems, the spec) and passes through
the ``nomention`` filter.
"""

import json
import sqlite3
from collections.abc import Sequence

from orchestrator.templates import render_template
from orchestrator.trace.writer import TraceWriter
from orchestrator.version import factory_version

TEMPLATE = "pr_body.md.j2"
ROLE_ORDER = ("planner", "builder", "reviewer")
VIOLATIONS_SHOWN = 2


def gate_evidence(row: sqlite3.Row) -> str:
    """What a reader needs to see for a gate row: violations, command, or the check count."""
    parts: list[str] = []
    violations = json.loads(row["violations_json"]) if row["violations_json"] else []
    if violations:
        shown = "; ".join(
            f"{v.get('item')}: {v.get('problem')}" for v in violations[:VIOLATIONS_SHOWN]
        )
        rest = len(violations) - VIOLATIONS_SHOWN
        parts.append(shown + (f" (+{rest} more)" if rest > 0 else ""))
    if row["command"]:
        parts.append(f"`{row['command']}` exit {row['exit_code']}")
    if not parts:
        checks = json.loads(row["checks_json"]) if row["checks_json"] else []
        parts.append(f"{len(checks)} check(s)")
    return "; ".join(parts)


def _phase_rows(rows: Sequence[sqlite3.Row]) -> list[dict[str, object]]:
    return [
        {
            "name": row["name"],
            "owner": row["owner"],
            "harness": row["harness"] or "",
            "model_id": row["model_id"] or "",
            "attempt": row["attempt"],
            "status": row["status"],
            "tokens_in": int(row["tokens_in"] or 0),
            "tokens_out": int(row["tokens_out"] or 0),
            "tokens_cache_read": int(row["tokens_cache_read"] or 0),
            "tokens_cache_write": int(row["tokens_cache_write"] or 0),
            "cost": f"{float(row['cost_usd'] or 0.0):.4f}",
            "session_id": row["session_id"] or "",
        }
        for row in rows
    ]


def _roles(rows: Sequence[sqlite3.Row]) -> list[dict[str, object]]:
    seen: dict[str, dict[str, object]] = {}
    for row in rows:
        owner = str(row["owner"])
        if owner in ROLE_ORDER and owner not in seen and row["harness"]:
            seen[owner] = {
                "role": owner,
                "harness": row["harness"],
                "harness_version": row["harness_version"] or "",
                "model_id": row["model_id"] or "",
            }
    return [seen[role] for role in ROLE_ORDER if role in seen]


def pr_body_fields(
    writer: TraceWriter,
    *,
    task_id: str,
    run_id: str,
    spec_rel: str,
    spec_text: str,
    branch: str,
    sha: str,
) -> dict[str, object]:
    task = writer.get_task(task_id)
    installation = writer.get_installation()
    if task is None or installation is None:
        raise ValueError(f"task {task_id} or the installation row is missing")
    org, repo = installation["org"], installation["repo"]
    phase_rows = writer.phases_for_run(run_id)
    verdict = writer.latest_log_payload(run_id, "verdict_policy") or {}
    strongest = verdict.get("strongest")
    findings = verdict.get("findings")
    dismissed = verdict.get("dismissed")
    return {
        "issue_number": task["issue_number"],
        "task_id": task_id,
        "attempt": task["attempt"],
        "factory_version": factory_version(),
        "spec_rel": spec_rel,
        "spec_url": f"https://github.com/{org}/{repo}/blob/{branch}/{spec_rel}",
        "spec_sha": (task["spec_sha"] or "")[:12],
        "spec_text": spec_text.strip(),
        "risk": task["risk"] or "unclassified",
        "risk_score": task["risk_score"],
        "branch": branch,
        "head_sha": sha,
        "verdict": verdict.get("verdict"),
        "conformance": None
        if verdict.get("conformance") is None
        else f"{float(str(verdict['conformance'])):.1f}",
        "strongest": strongest if isinstance(strongest, dict) else None,
        "revisions": sum(1 for row in phase_rows if row["name"] == "revision"),
        "gates": [
            {
                "gate": row["gate"],
                "attempt": row["attempt"],
                "passed": bool(row["passed"]),
                "evidence": gate_evidence(row),
            }
            for row in writer.gate_results_for_run(run_id)
        ],
        "phases": _phase_rows(phase_rows),
        "roles": _roles(phase_rows),
        "total_cost": f"{sum(float(r['cost_usd'] or 0.0) for r in phase_rows):.4f}",
        "total_tokens_in": sum(int(r["tokens_in"] or 0) for r in phase_rows),
        "total_tokens_out": sum(int(r["tokens_out"] or 0) for r in phase_rows),
        "advisory": [f for f in findings if isinstance(f, dict)]
        if isinstance(findings, list)
        else [],
        "dismissed": [d for d in dismissed if isinstance(d, dict)]
        if isinstance(dismissed, list)
        else [],
    }


def render_pr_body(fields: dict[str, object]) -> str:
    return render_template(TEMPLATE, **fields)
