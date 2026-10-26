"""Phase bookkeeping for one run: rows, start and end events, the sequence counter.

``PlanApprovalPhase`` and ``ReviewPhase`` use it; ``BuildPhase`` and
``PlanPhase`` carry their own copies until the scheduled refactor moves
them here.
"""

from orchestrator.harness.base import HarnessResult
from orchestrator.models.enums import EventKind, PhaseKind, PhaseName, PhaseStatus
from orchestrator.trace.writer import TraceWriter


class PhaseRecorder:
    def __init__(
        self, writer: TraceWriter, *, task_id: str, run_id: str, seq_start: int = 0
    ) -> None:
        self.writer = writer
        self.task_id = task_id
        self.run_id = run_id
        self._seq = seq_start

    @property
    def next_seq(self) -> int:
        return self._seq

    def event(
        self,
        kind: EventKind,
        payload: object,
        *,
        phase_id: str | None,
        actor: str = "orchestrator",
    ) -> int:
        return self.writer.write_event(
            kind,
            actor,
            task_id=self.task_id,
            run_id=self.run_id,
            phase_id=phase_id,
            payload=payload,
        )

    def start(
        self,
        name: PhaseName,
        kind: PhaseKind,
        owner: str,
        *,
        attempt: int,
        timeout_s: int,
        **extra: object,
    ) -> str:
        self.writer.require_enabled()
        self._seq += 1
        phase_id = self.writer.start_phase(
            run_id=self.run_id,
            seq=self._seq,
            name=name,
            kind=kind,
            owner=owner,
            timeout_s=timeout_s,
            attempt=attempt,
            **extra,  # type: ignore[arg-type]
        )
        self.event(
            EventKind.PHASE_START, {"name": str(name), "attempt": attempt}, phase_id=phase_id
        )
        return phase_id

    def finish(
        self,
        phase_id: str,
        status: PhaseStatus,
        *,
        error: str | None = None,
        result: HarnessResult | None = None,
    ) -> None:
        if result is None:
            self.writer.finish_phase(phase_id, status, error=error)
        else:
            cost = result.usage.cost_usd
            if result.cost_is_session_total and result.session_id:
                # a resumed session reports its running total: book only this call's part, so
                # retries and repair rounds do not count earlier spend again (task and day caps)
                cost = max(0.0, cost - self.writer.session_cost_usd(result.session_id))
            self.writer.finish_phase(
                phase_id,
                status,
                tokens_in=result.usage.input_tokens,
                tokens_out=result.usage.output_tokens,
                tokens_cache_read=result.usage.cache_read_tokens,
                tokens_cache_write=result.usage.cache_write_tokens,
                cost_usd=cost,
                retries=result.retries,
                session_id=result.session_id,
                error=error,
            )
        self.event(EventKind.PHASE_END, {"status": str(status), "error": error}, phase_id=phase_id)
