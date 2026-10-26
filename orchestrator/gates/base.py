"""The shape every gate returns and the one place gate rows are written."""

from collections.abc import Sequence
from dataclasses import dataclass, field

from orchestrator import constants
from orchestrator.models.enums import EventKind, GateName
from orchestrator.trace.writer import TraceWriter


@dataclass(frozen=True)
class Check:
    item: str
    ok: bool
    note: str = ""


@dataclass(frozen=True)
class Violation:
    item: str
    problem: str


@dataclass(frozen=True)
class GateResult:
    gate: GateName
    passed: bool
    checks: tuple[Check, ...] = ()
    violations: tuple[Violation, ...] = ()
    command: str | None = None
    exit_code: int | None = None
    output_tail: str | None = None

    def correction_text(self) -> str:
        """The violation list handed back to the agent on a repair (PRD §Capability 3)."""
        state = "passed" if self.passed else "failed"
        lines = [f"Gate {self.gate} {state}."]
        lines += [f"- {v.item}: {v.problem}" for v in self.violations]
        if self.command is not None:
            lines.append(f"Command: {self.command} (exit code {self.exit_code})")
        if self.output_tail:
            tail = self.output_tail[-constants.OUTPUT_TAIL_CHARS :]
            lines.append("Last output:\n" + tail)
        return "\n".join(lines)


def passed(
    gate: GateName,
    checks: Sequence[Check],
    *,
    command: str | None = None,
    exit_code: int | None = None,
    output_tail: str | None = None,
) -> GateResult:
    return GateResult(
        gate=gate,
        passed=True,
        checks=tuple(checks),
        command=command,
        exit_code=exit_code,
        output_tail=output_tail,
    )


def failed(
    gate: GateName,
    violations: Sequence[Violation],
    checks: Sequence[Check] = (),
    *,
    command: str | None = None,
    exit_code: int | None = None,
    output_tail: str | None = None,
) -> GateResult:
    return GateResult(
        gate=gate,
        passed=False,
        checks=tuple(checks),
        violations=tuple(violations),
        command=command,
        exit_code=exit_code,
        output_tail=output_tail,
    )


@dataclass(frozen=True)
class GateContext:
    task_id: str
    run_id: str
    phase_id: str
    attempt: int = 1
    actor: str = "orchestrator"
    extra: dict[str, object] = field(default_factory=dict)


def write_gate(writer: TraceWriter, result: GateResult, ctx: GateContext) -> str:
    """Write the ``gate_results`` row and its gate event; return the row id."""
    gate_id = writer.write_gate_result(
        run_id=ctx.run_id,
        phase_id=ctx.phase_id,
        attempt=ctx.attempt,
        gate=result.gate,
        passed=result.passed,
        checks=[{"item": c.item, "ok": c.ok, "note": c.note} for c in result.checks],
        violations=[{"item": v.item, "problem": v.problem} for v in result.violations] or None,
        command=result.command,
        exit_code=result.exit_code,
        output_tail=result.output_tail,
    )
    writer.write_event(
        EventKind.GATE_PASS if result.passed else EventKind.GATE_FAIL,
        ctx.actor,
        task_id=ctx.task_id,
        run_id=ctx.run_id,
        phase_id=ctx.phase_id,
        payload={
            "gate": str(result.gate),
            "gate_results_id": gate_id,
            "attempt": ctx.attempt,
            "violations": len(result.violations),
            "command": result.command,
            "exit_code": result.exit_code,
        },
    )
    return gate_id
