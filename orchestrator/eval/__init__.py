"""The eval runner: a set of closed issues replayed on ``eval/scratch`` (PRD §Capability 5)."""

from orchestrator.eval.runner import (
    EvalError,
    EvalItem,
    EvalReport,
    EvalRunner,
    ItemResult,
    delta_points,
    ensure_eval_set,
    pass_rates,
)

__all__ = [
    "EvalError",
    "EvalItem",
    "EvalReport",
    "EvalRunner",
    "ItemResult",
    "delta_points",
    "ensure_eval_set",
    "pass_rates",
]
