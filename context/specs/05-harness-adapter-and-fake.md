# Unit 05: Harness adapter contract and fake harness

## Goal

Define the one contract every harness implements (prompt and schema in;
envelope, session id and usage out; tool calls streamed; resume by session
id; the process tree killed on timeout), the subprocess runner that enforces
it on Windows and POSIX, and a `fake` harness that replays recorded scripts
so every later unit runs offline.

## Design

No UI. Visible result: a fake run streams tool calls into the trace in order,
applies its file effects in a temp workspace, returns an envelope and usage;
a sleeping child process tree is killed within the grace period on this
platform.

## Implementation

### orchestrator/harness/base.py

- `HarnessRequest` (role, prompt, system_prompt, output_schema, cwd, env,
  tools, model, thinking, max_turns, budget_usd, timeout_s,
  resume_session_id), `Usage`, `ToolCall`, `HarnessResult` (envelope_text,
  session_id, usage, harness, harness_version, exit_code, outcome in
  `completed | timeout | killed | error`, stderr_tail, error), the
  `ToolCallSink` and `ProcessSink` callbacks, and the `HarnessAdapter`
  protocol with `name`, `version()` and `async run(...)`.

### orchestrator/harness/process.py

- `spawn(argv, cwd, env)` with a new session (POSIX) or a new process group
  (Windows), explicit argv and env, pipes for stdout and stderr.
- `terminate_tree(proc, grace_s)`: `killpg` SIGTERM then SIGKILL on POSIX;
  `taskkill /T` then `taskkill /T /F` on Windows.
- `run_with_timeout(argv, ..., timeout_s, on_stdout_line, stdin_text)`:
  streams stdout lines to a callback, keeps the last 4,000 characters of
  stderr, kills the tree on timeout, returns `ProcessOutcome(exit_code,
  timed_out, stderr_tail, duration_s)`.

### orchestrator/harness/sink.py

- `trace_tool_call_sink(writer, task_id, run_id, phase_id, actor,
  process_id)` writing one `tool_call` event per call with the TRD §9
  payload (`result_snippet` capped at 1,000 characters) and touching the
  process row.

### orchestrator/harness/fake.py

- `FakeScript` (session_id, tool_calls, effects, envelope or
  envelope_text, usage, sleep_s, exit_code, fail_with) loaded from JSON;
  `FakeHarness(scripts)` consumes one script per call and repeats the last;
  effects `write` and `delete` are applied under `cwd` only (no absolute
  paths, no `..`); `sleep_s > 0` runs a real sleeping subprocess through
  `run_with_timeout` so timeouts are exercised for real.

### orchestrator/models/settings.py

- `subprocess_base_env()`: the only place the process environment is read
  for subprocesses, an allow-list (`PATH`, `HOME`, `USERPROFILE`,
  `SYSTEMROOT`, `TEMP`, `TMP`, `LANG`, `LC_ALL`).

### fixtures/envelopes/builder_green.json

- A green builder script: three tool calls, two file effects, a valid
  `BuildOutput`, realistic usage.

## Dependencies

- dev: `pytest-asyncio` (async tests, `asyncio_mode = "auto"`)

## Verify when done

- [ ] A fake run returns the fixture's envelope text, session id and usage,
      streams three tool calls in order, and writes the two files
- [ ] Tool calls reach the trace as `tool_call` events with the TRD payload
      and the process row is touched
- [ ] Scripts are consumed in order and the last one repeats
- [ ] An effect with an absolute path or `..` is refused
- [ ] A script with `sleep_s = 30` under a 1 s timeout returns
      `outcome = "timeout"` in under 10 s
- [ ] A parent that spawns a child is killed with its child within the grace
      period on this platform
- [ ] `run_with_timeout` delivers stdout lines and caps the stderr tail
- [ ] The four quality commands pass
