# Unit 26: Codex adapter

## Goal

Run Codex CLI through the same harness contract as Claude Code
(PRD §Capability 3; TRD §2.0): prompt and schema in; envelope, session id
and usage out; resume by session id; the process tree dies on timeout. The
shipped roster runs the reviewer on `codex exec --sandbox read-only`.

## Design

No UI. Visible result: `fixtures/streams/codex_*.jsonl` replay through the
adapter into the same `HarnessResult` and `ToolCall` rows the Claude Code
adapter produces; one contract test suite passes for `fake`, `claude_code`
and `codex`.

## Implementation

### Facts verified on 2026-10-24

From the CLI reference, the non-interactive guide and
`codex-rs/exec/src/{cli,lib,exec_events}.rs`:

- `codex exec [PROMPT]`; `-` reads the prompt from stdin; `codex exec resume
  <SESSION_ID> -` continues a thread. `--json` prints JSONL events to
  stdout; everything else goes to stderr; `--output-schema <FILE>` is a JSON
  Schema file for the final message; `--sandbox read-only |
  workspace-write | danger-full-access`; `-m <model>`; `-C <dir>`;
  `--skip-git-repo-check`; `-c key=value` overrides (`approval_policy`,
  `model_reasoning_effort`). `--full-auto` is gone. No system-prompt,
  max-turns or budget flag exists.
- Events: `thread.started{thread_id}`, `turn.started`,
  `item.started|updated|completed{item}`, `turn.completed{usage}`,
  `turn.failed{error{message}}`, `error{message}`. Items: `agent_message`,
  `reasoning`, `command_execution{command, aggregated_output, exit_code,
  status}`, `file_change{changes[{path, kind}], status}`,
  `mcp_tool_call{server, tool, arguments, result, error, status}`,
  `web_search{query}`, `todo_list`, `error{message}`. Usage:
  `input_tokens`, `cached_input_tokens`, `cache_write_input_tokens`,
  `output_tokens`, `reasoning_output_tokens`. Exit code 1 after a failed
  turn or an unretried error.
- Auth: `CODEX_API_KEY` for one invocation (`OPENAI_API_KEY` still read by
  the CLI). Sessions live under `CODEX_HOME` (default `~/.codex`), which
  resume needs.

### orchestrator/harness/codex.py

- `CodexStreamParser`: thread id, turns, summed usage, retried `error`
  events, `turn.failed`, the last `agent_message` (fences stripped) as the
  envelope; tool items paired by id (`shell`, `apply_patch`,
  `mcp:<server>.<tool>`, `web_search`), `ok` from status and exit code.
- `CodexHarness(binary, runner, version_probe)`: `version()` from
  `codex --version`; `build_argv(request, schema_path)`; the schema is
  written to a temporary directory outside the worktree for the run;
  sandbox `read-only` for the reviewer, `workspace-write` otherwise; the
  system prompt is folded above the user prompt because the CLI has no
  flag for it; `max_turns` and `budget_usd` are not enforceable by the CLI
  (the timeout and, from phase 3, the proxy cap bound the phase).
- Outcomes: timeout; `turn.failed` → error with the message; no
  `turn.completed` → killed on SIGTERM else error; otherwise completed,
  errors before completion counted as retries, exit code 1 → error.

### Elsewhere

- `registry.make_harness` builds `CodexHarness(settings.codex_bin)`.
- `AGENT_ENV_PASSTHROUGH` adds `CODEX_API_KEY` and `CODEX_HOME`.
- `tests/unit/test_harness_contract.py`: the same assertions for the three
  adapters (completed run; timeout).

## Dependencies

- None new. The Codex CLI is not installed on this machine; the streams are
  hand-authored from the event schema and must be replaced by recordings
  from the user's first real run (`npm i -g @openai/codex`, `CODEX_API_KEY`).

## Verify when done

- [ ] `build_argv` is exact for a fresh run and a resume; the reviewer gets
      `read-only`, the builder `workspace-write`
- [ ] Green stream: thread id, usage (15230/1480/9000/0), one retry, two
      shell tool calls with ids and snippets, the envelope validates as
      `ReviewOutput`
- [ ] Failed turn → error with the message and a failed tool call; cut
      stream with exit 143 → killed, pending call flushed with `ok = None`;
      other exit → error; runner timeout → timeout keeping the resume id
- [ ] The parser names mcp, patch and search tools, strips fences, sums
      usage across turns, counts unparsable lines
- [ ] Contract suite green for `fake`, `claude_code`, `codex`
- [ ] The four quality commands pass
