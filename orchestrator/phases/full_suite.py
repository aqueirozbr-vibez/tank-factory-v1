"""The full suite with the holdout on the final SHA, run by code (PRD §Capability 5; TRD §7.5).

The holdout acceptance tests enter the pipeline only here: copied into the
worktree for this phase alone (worktree mode) or mounted read-only into a
fresh container from the task image that no agent ever ran in (container
mode), with no virtual key in the environment. A failure buys one builder
repair counted against the repair cap; a second failure blocks the task
with the failing tests in the comment.
"""

import re
import shutil
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from orchestrator import constants
from orchestrator.gates.base import GateContext, GateResult, write_gate
from orchestrator.gates.quality import run_full_suite
from orchestrator.models.enums import BlockedReason, EventKind, PhaseKind, PhaseName, PhaseStatus
from orchestrator.models.envelopes import VerifyOutput
from orchestrator.models.settings import Settings
from orchestrator.phases.build import BuildDeps, BuildPhase, verify_from
from orchestrator.phases.recorder import PhaseRecorder
from orchestrator.workspace.container import MOUNT_WORK, DockerCli, Mount, create_container
from orchestrator.workspace.git import head_sha, run_git
from orchestrator.workspace.worktree import Worktree

ACCEPTANCE_REL = "tests/acceptance"
FAILED_LINE = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.MULTILINE)


class HoldoutSource(Protocol):
    """What the phase needs from the holdout checkout."""

    @property
    def tests_dir(self) -> Path: ...

    def refresh(self) -> str: ...

    def copy_into(self, workspace: Path) -> Path: ...


@dataclass(frozen=True)
class FullSuiteOutcome:
    status: Literal["pass", "blocked"]
    run_id: str
    head_sha: str | None
    repairs: int
    failing_tests: tuple[str, ...]
    blocked_reason: BlockedReason | None = None
    evidence: str = ""

    @property
    def passed(self) -> bool:
        return self.status == "pass"


def failing_tests(output: str) -> list[str]:
    """The test ids a pytest-style summary names, in order, once each."""
    seen: list[str] = []
    for match in FAILED_LINE.finditer(output):
        test_id = match.group(1)
        if test_id not in seen:
            seen.append(test_id)
    return seen


@contextmanager
def holdout_copied(holdout: HoldoutSource, workspace: Path) -> Iterator[Path]:
    """Copy ``tests/acceptance`` in for this phase only; the worktree leaves as it came."""
    destination = holdout.copy_into(workspace)
    try:
        yield destination
    finally:
        run_git(["checkout", "-q", "--", ACCEPTANCE_REL], cwd=workspace, check=False)
        run_git(["clean", "-fdq", "--", ACCEPTANCE_REL], cwd=workspace, check=False)
        if destination.is_dir() and not any(destination.iterdir()):
            shutil.rmtree(destination, ignore_errors=True)


def repair_request(result: GateResult, failing: Sequence[str]) -> VerifyOutput:
    """What the builder gets: the failing holdout tests first, then the command evidence."""
    base = verify_from(result)
    violations = [f"failing test: {test_id}" for test_id in failing] + base.violations
    return base.model_copy(update={"violations": violations})


class FullSuitePhase:
    def __init__(
        self,
        deps: BuildDeps,
        *,
        task_id: str,
        worktree: Worktree,
        spec_rel: str,
        run_id: str,
        holdout: HoldoutSource,
        issue_number: int,
        attempt: int = 1,
        build_repairs: int = 0,
        docker: DockerCli | None = None,
        task_image: str | None = None,
        network: str | None = None,
        seq_start: int | None = None,
    ) -> None:
        self.deps = deps
        self.writer = deps.writer
        self.task_id = task_id
        self.worktree = worktree
        self.spec_rel = spec_rel
        self.run_id = run_id
        self.holdout = holdout
        self.issue_number = issue_number
        self.attempt = attempt
        self.build_repairs = build_repairs
        self.docker = docker
        self.task_image = task_image
        self.network = network
        start = self.writer.last_phase_seq(run_id) if seq_start is None else seq_start
        self.recorder = PhaseRecorder(self.writer, task_id=task_id, run_id=run_id, seq_start=start)

    # --- one suite run ---------------------------------------------------------------------

    def _env(self) -> dict[str, str]:
        """The base allow-list only: no virtual key reaches the suite (TRD §7.5)."""
        return Settings.subprocess_base_env()

    async def _in_worktree(self) -> GateResult:
        with holdout_copied(self.holdout, self.worktree.path):
            return await run_full_suite(
                self.worktree.path, self.deps.config.quality, env=self._env()
            )

    async def _in_container(self, docker: DockerCli, image: str, *, phase_id: str) -> GateResult:
        name = f"factory-suite-{self.issue_number}-{self.attempt}"
        holdout_mount = Mount(str(self.holdout.tests_dir), f"{MOUNT_WORK}/{ACCEPTANCE_REL}", "ro")
        container = await create_container(
            docker,
            image=image,
            name=name,
            worktree=self.worktree,
            network=self.network,
            task_id=self.task_id,
            extra_mounts=(holdout_mount,),
        )
        self.recorder.event(
            EventKind.LOG,
            {
                "container": name,
                "image": image,
                "holdout_mount": holdout_mount.flag(),
                "virtual_key": None,
            },
            phase_id=phase_id,
        )
        try:
            return await run_full_suite(
                self.worktree.path,
                self.deps.config.quality,
                env=self._env(),
                runner=docker.exec_runner(container),
            )
        finally:
            await docker.remove(name)
            self.recorder.event(
                EventKind.LOG, {"container": name, "removed": True}, phase_id=phase_id
            )

    async def _suite(self, phase_id: str, attempt: int) -> GateResult:
        holdout_sha = self.holdout.refresh()
        self.recorder.event(
            EventKind.LOG,
            {"holdout_sha": holdout_sha, "head_sha": head_sha(self.worktree.path)},
            phase_id=phase_id,
        )
        if self.docker is not None and self.task_image is not None:
            result = await self._in_container(self.docker, self.task_image, phase_id=phase_id)
        else:
            result = await self._in_worktree()
        ctx = GateContext(
            task_id=self.task_id, run_id=self.run_id, phase_id=phase_id, attempt=attempt
        )
        write_gate(self.writer, result, ctx)
        return result

    # --- the phase -----------------------------------------------------------------------

    async def run(self) -> FullSuiteOutcome:
        repairs = 0
        while True:
            attempt = self.attempt + repairs
            phase_id = self.recorder.start(
                PhaseName.FULL_SUITE,
                PhaseKind.CODE,
                "orchestrator",
                attempt=attempt,
                timeout_s=constants.BUILD_TIMEOUT_S,
            )
            result = await self._suite(phase_id, attempt)
            if result.passed:
                self.recorder.finish(phase_id, PhaseStatus.PASS)
                return FullSuiteOutcome(
                    "pass", self.run_id, head_sha(self.worktree.path), repairs, ()
                )
            failing = tuple(failing_tests(result.output_tail or ""))
            self.recorder.finish(phase_id, PhaseStatus.FAIL, error="full_suite")
            repairs_before = self.build_repairs + repairs
            if repairs < constants.FULL_SUITE_REPAIR_CAP and repairs_before < constants.REPAIR_CAP:
                repairs += 1
                self.recorder.event(
                    EventKind.LOG,
                    {
                        "full_suite_repair": repairs,
                        "repairs_counted": repairs_before + 1,
                        "failing_tests": list(failing),
                    },
                    phase_id=phase_id,
                )
                build = await BuildPhase(
                    self.deps,
                    task_id=self.task_id,
                    worktree=self.worktree,
                    spec_rel=self.spec_rel,
                    run_id=self.run_id,
                    initial_verify=repair_request(result, failing),
                    repairs_used=repairs_before + 1,
                    seq_start=self.recorder.next_seq,
                ).run()
                self.recorder = PhaseRecorder(
                    self.writer,
                    task_id=self.task_id,
                    run_id=self.run_id,
                    seq_start=self.writer.last_phase_seq(self.run_id),
                )
                if not build.passed:
                    return FullSuiteOutcome(
                        "blocked",
                        self.run_id,
                        None,
                        repairs,
                        failing,
                        build.blocked_reason or BlockedReason.REPAIR_CAP,
                        build.evidence,
                    )
                continue
            named = ", ".join(failing[:10]) if failing else "see the command output"
            when = "after one builder repair" if repairs else "with no repair left in the cap"
            evidence = (
                f"Full suite failed {when}: {len(failing)} failing test(s): {named}\n"
                f"{result.correction_text()}"
            )
            self.recorder.event(
                EventKind.ERROR,
                {
                    "blocked_reason": str(BlockedReason.FULL_SUITE),
                    "failing_tests": list(failing),
                    "repairs": repairs,
                },
                phase_id=phase_id,
            )
            return FullSuiteOutcome(
                "blocked", self.run_id, None, repairs, failing, BlockedReason.FULL_SUITE, evidence
            )
