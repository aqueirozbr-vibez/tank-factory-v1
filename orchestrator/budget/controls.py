"""The one-time budget raise (PRD §Capability 5; TRD §6.1 ``POST /tasks/{id}/budget``).

A write-access user may raise one task's budget once, by at most
``BUDGET_RAISE_MAX_FACTOR``, with a reason; the raise is one ``control``
event the resume rule looks for before a budget block may resume.
"""

from dataclasses import dataclass
from typing import Literal

from orchestrator import constants
from orchestrator.models.enums import EventKind
from orchestrator.trace.writer import TraceWriter

RaiseCode = Literal["raised", "not_found", "already_raised", "not_an_increase", "over_factor"]


@dataclass(frozen=True)
class RaiseOutcome:
    code: RaiseCode
    message: str
    old_budget_usd: float | None = None
    new_budget_usd: float | None = None
    event_seq: int | None = None

    @property
    def ok(self) -> bool:
        return self.code == "raised"

    @property
    def http_status(self) -> int:
        return {
            "raised": 200,
            "not_found": 404,
            "already_raised": 409,
            "not_an_increase": 422,
            "over_factor": 422,
        }[self.code]


def raise_task_budget(
    writer: TraceWriter, *, task_id: str, new_budget_usd: float, actor: str, reason: str
) -> RaiseOutcome:
    task = writer.get_task(task_id)
    if task is None:
        return RaiseOutcome("not_found", f"task {task_id} is not admitted")
    old = float(task["budget_usd"])
    if writer.count_controls(task_id, "budget_raise") > 0:
        return RaiseOutcome("already_raised", "this task's budget was raised once already", old)
    if new_budget_usd <= old:
        return RaiseOutcome(
            "not_an_increase", f"the new budget must exceed the current {old:.2f}", old
        )
    limit = old * constants.BUDGET_RAISE_MAX_FACTOR
    if new_budget_usd > limit + 1e-9:
        return RaiseOutcome(
            "over_factor",
            f"a raise is at most {constants.BUDGET_RAISE_MAX_FACTOR:g}x the current budget"
            f" ({limit:.2f})",
            old,
        )
    writer.set_task_budget(task_id, new_budget_usd)
    seq = writer.write_event(
        EventKind.CONTROL,
        actor,
        task_id=task_id,
        payload={
            "action": "budget_raise",
            "old": old,
            "new": new_budget_usd,
            "factor": round(new_budget_usd / old, 4),
            "reason": reason,
        },
    )
    return RaiseOutcome(
        "raised", f"budget raised from {old:.2f} to {new_budget_usd:.2f}", old, new_budget_usd, seq
    )
