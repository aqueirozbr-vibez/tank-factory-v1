# Unit 06: Claude Code adapter

## Goal

Drive the Claude Code CLI as a subprocess through the unit 05 contract:
build the exact argv for `claude -p`, stream `stream-json` into tool-call
rows, read the final `result` message into an envelope, usage and cost, and
record the harness version from the binary. No network in tests.

## Design

No UI. Visible result: a recorded stream replays into the expected tool
calls and result; the argv for a request is exact; the bundled binary on
this machine answers `--version` offline.

Facts verified against the CLI reference and headless docs
(code.claude.com, 2026-10-24) and the installed binary 2.1.309:

- Print mode: `-p` with the prompt on stdin; `--output-format stream-json`
  requires `--verbose`; `--json-schema` returns the validated object in the
  result message's `structured_output`; `--max-turns`, `--max-budget-usd`
  (print mode only); `--resume <session_id>`; `--model <id>`; `--tools` to
  restrict and `--allowedTools` to pre-approve; `--permission-mode dontAsk`
  with `--permission-prompts none` for unattended runs; `--bare` skips hooks,
  skills, MCP, memory and `CLAUDE.md` and reads `ANTHROPIC_API_KEY` from the
  environment; `--system-prompt` replaces the default prompt.
- Stream messages: `system`/`init` (`session_id`, `model`,
  `claude_code_version`, `tools`); `assistant` with `message.content`
  blocks of type `tool_use` (`id`, `name`, `input`) and `text`; `user` with
  `tool_result` blocks (`tool_use_id`, `content`, `is_error`); `result` with
  `subtype` in `success`, `error_max_turns`, `error_during_execution`,
  `error_max_budget_usd`, `error_max_structured_output_retries`, plus
  `is_error`, `num_turns`, `duration_ms`, `total_cost_usd`, `usage`
  (`input_tokens`, `output_tokens`, `cache_read_input_tokens`,
  `cache_creation_input_tokens`), `structured_output`, `errors[]`,
  `permission_denials[]`; `system`/`api_retry` for retries.
- SIGTERM exits 143 and leaves no result message. `claude --version`
  prints `2.1.309 (Claude Code)`.

## Implementation

### orchestrator/harness/claude_code.py

- `ClaudeCodeHarness(binary, bare=True, runner=run_with_timeout,
  version_probe=None)`.
- `build_argv(request)`: `[binary, "-p", "--output-format", "stream-json",
  "--verbose", "--json-schema", <schema json>, "--model", <model>,
  "--permission-mode", "dontAsk", "--permission-prompts", "none"]` plus
  `--bare` when enabled, `--max-turns`, `--max-budget-usd`, `--tools` and
  `--allowedTools` from the request's tools, `--system-prompt` when given,
  `--resume` when resuming. The prompt goes to stdin.
- `StreamParser`: pairs `tool_use` with its `tool_result` and emits one
  `ToolCall` per pair (ok = not `is_error`, snippet from the result
  content, duration from wall clock); flushes unmatched calls at the end
  with `ok = None`; counts `api_retry` events and unparsable lines; keeps
  the init metadata and the result message.
- Result mapping: timeout → `timeout`; no result message → `killed` when
  the exit code is 143 or absent, else `error`; `success` with
  `structured_output` → `completed` and the envelope as JSON text; any
  other subtype → `error` carrying the subtype and `errors`. Usage and
  cost are taken from the result whenever one exists.
- `version()`: runs `<binary> --version` once and keeps the first token.

### orchestrator/harness/base.py

- `HarnessResult` gains `num_turns` and `retries`.

### orchestrator/models/settings.py

- `claude_bin` (`FACTORY_CLAUDE_BIN`, default `claude`), `claude_bare`
  (`FACTORY_CLAUDE_BARE`, default true), `codex_bin`.

### fixtures/streams/

- `claude_build_green.jsonl`, `claude_build_max_turns.jsonl`,
  `claude_build_cut.jsonl`. Hand-authored to the documented shapes; the
  phase 1 gate's real run replaces them with recordings.

## Dependencies

- None new.

## Verify when done

- [ ] `build_argv` for a builder request equals the documented flag list
      with the tools, caps, schema, model and resume id in place
- [ ] The green stream yields four tool calls in order (`Read`, `Edit`,
      `Bash`, `StructuredOutput`), `completed`, the `BuildOutput` envelope
      text, session id, cost 0.4138 and the four token counts
- [ ] The max-turns stream yields `error` naming `error_max_turns` and still
      reports usage and cost
- [ ] A stream cut before the result with exit 143 yields `killed`; a
      runner timeout yields `timeout`
- [ ] Unparsable lines are counted and do not abort; an unmatched
      `tool_use` is flushed with `ok = None`
- [ ] `version()` parses `2.1.309 (Claude Code)` to `2.1.309`; against the
      bundled binary on this machine it returns a version offline
- [ ] The four quality commands pass
