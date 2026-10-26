"""A failure before the run row (workspace, reconciliation) blocks the task instead of hanging."""

from pathlib import Path

from orchestrator.models.enums import BlockedReason, TaskStatus
from orchestrator.trace import TraceWriter
from tests.unit.test_build_phase import GREEN
from tests.unit.test_review_phase import ACCEPTED
from tests.unit.test_runner import PLAN, Stack, prepare
from tests.unit.test_spec_gate import TASK


async def test_a_workspace_failure_before_the_run_row_blocks_the_task(
    product: Path, tmp_path: Path, trace_writer: TraceWriter
) -> None:
    prepare(product, tmp_path)
    stack = Stack(trace_writer, product, tmp_path, [PLAN, GREEN, ACCEPTED])
    stack.admit()
    worktrees = stack.settings.worktrees_dir
    worktrees.parent.mkdir(parents=True, exist_ok=True)
    worktrees.write_text("not a directory\n", encoding="utf-8")  # no worktree can be created
    report = await stack.runner.run(TASK)
    assert report.blocked_reason is BlockedReason.TIMEOUT and report.notified
    assert report.run_id == ""  # no run row: the failure came before it
    task = stack.task()
    assert task["status"] == str(TaskStatus.BLOCKED)
    assert task["blocked_reason"] == str(BlockedReason.TIMEOUT)
    errors = trace_writer.db.fetchall(
        "SELECT payload_json FROM events WHERE kind = 'error' AND task_id = ?", (TASK,)
    )
    assert len(errors) == 1 and "before the run" in str(errors[0]["payload_json"])
    assert "**Blocked**: `timeout`" in stack.github.comments()[-1]
    assert stack.github.labels_added() == ["factory:needs-human"]
