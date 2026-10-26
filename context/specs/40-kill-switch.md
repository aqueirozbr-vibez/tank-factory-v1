# Unit 40: Kill-switch

## Goal

`FACTORY_ENABLED=0` stops every live process within 60 s: the agent process
receives SIGTERM, the container is killed after the grace period, the task
becomes `cancelled`, the branch stays and a comment names the last
completed phase; the flag is checked before every phase; re-enabling is a
confirmed control with a reason that re-admits nothing (PRD §Capability 1
· cancel, §Capability 5 · Kill-switch and Re-enabling; TRD §6.1
`POST /controls/kill-switch`, §7.6, §11 fail-closed).

## Design

Terminal only. The flag of record is `installation.factory_enabled`;
`FACTORY_ENABLED` in the environment seeds it at service start. Every phase
starter calls `writer.require_enabled()`, which treats an unreadable or
missing row as 0, so a phase that was about to start after the switch
flipped is refused and its task cancelled. The running orchestrator owns
the fan-out: `KillSwitch.disable` writes the flag, cancels every live task
through the supervisor (the runner's own cancellation path terminates the
process tree and removes the container), fails the open phase and process
rows, marks the tasks `cancelled`, posts the comment, kills any container
still labelled `factory.task`, and writes one `control` event carrying the
report. A watchdog loop re-reads the flag every `KILL_SWITCH_POLL_S`
seconds so a flag written by another process is honoured within the bound,
and the scheduler dispatches nothing while the flag is 0. `factory stop`
and `factory start` call `POST /controls/kill-switch` on the local
orchestrator (loopback callers only until the console token lands in
phase 5, actor from `X-Factory-Actor`), and fall back to writing the flag
directly, killing labelled containers, when no orchestrator answers.

## Implementation

- `orchestrator/errors.py`: `FactoryDisabledError`.
- `orchestrator/trace/writer.py`: `factory_enabled`, `require_enabled`,
  `running_task_ids`, `count_open_processes`.
- `orchestrator/watchdogs.py`: `KillSwitch(writer, supervisor, docker,
  github, interval_s)` with `enabled`, `disable`, `enable`, `run`;
  `StopReport`.
- `orchestrator/api/app.py`: `POST /controls/kill-switch` (`{enabled,
  reason}` → `{factory_enabled, live_processes, event_seq, cancelled,
  containers, elapsed_s}`; 400, 403 non-loopback, 409 already in state,
  503 when this process holds no switch).
- `cli/kill_switch.py`, `factory stop|start --reason [--by] [--data-dir]`.
- Phase starters (`PhaseRecorder.start`, `BuildPhase._start_phase`,
  `PlanPhase._start_phase`) require the flag; the runner maps
  `FactoryDisabledError` to a `cancelled` report; `Scheduler.dispatch` is a
  no-op while disabled; `messages.cancelled` names the cause;
  `DockerCli.task_container_names`.

## Dependencies

- Unit 36. No new package (`httpx` is already a dependency).

## Verify when done

- [ ] With three live fake phases, `disable` ends all within 60 s and
      writes one `control` event plus three failed phase rows; the tasks
      are `cancelled`, their processes ended, each issue commented with the
      last completed phase
- [ ] A task whose phase starts while the flag is 0 is refused and
      cancelled; the scheduler dispatches nothing while disabled
- [ ] `enable` writes a control with the reason, sets the flag and leaves
      cancelled tasks cancelled
- [ ] The API endpoint enforces actor, body, loopback and state; the CLI
      `stop`/`start` work against the trace directly when no orchestrator
      runs
- [ ] A labelled container is killed and removed on `disable`
- [ ] The four quality commands pass
